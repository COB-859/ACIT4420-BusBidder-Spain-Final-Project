"""Command-line interface: the only module that prints."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from busbidder import __version__
from busbidder.booking import BOOKINGS_FILE, BookingStore, SeatLedger
from busbidder.constraints import filter_offers
from busbidder.display import (
    format_comparison,
    format_filter,
    format_preferences,
    format_ranking,
    format_removed,
    format_request,
)
from busbidder.exceptions import BusBidderError, DataFileError, InvalidRecordError, InvalidRequestError
from busbidder.loader import (
    PROFILES_FILE,
    REJECTED_FILE,
    LoadResult,
    load_all,
    load_profiles,
    write_rejected,
)
from busbidder.logsetup import close_logging, configure_logging
from busbidder.models import Destination, TimeBand, TravellerProfile, TravelRequest
from busbidder.ranking import check_weights, parse_weights, rank_offers
from busbidder.validation import parse_date

logger = logging.getLogger("busbidder.cli")

EXIT_OK = 0
EXIT_ERROR = 1
DEFAULT_DATA_DIR = Path("data")
DEFAULT_STATE_DIR = Path("state")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_TOP = 10


@dataclass
class Session:
    """Everything one command needs: the data, the profiles and the seats left."""

    data: LoadResult
    profiles: dict[str, TravellerProfile]
    store: BookingStore
    ledger: SeatLedger


def open_session(args: argparse.Namespace) -> Session:
    """Read the data files, the profiles and the saved bookings."""
    data = load_all(args.data)
    write_rejected(data.rejected, args.output / REJECTED_FILE)
    profiles = load_profiles(args.data / PROFILES_FILE)
    store = BookingStore(args.state / BOOKINGS_FILE)
    ledger = SeatLedger(data.departures, store.load())
    return Session(data, profiles, store, ledger)


def find_destination(text: str, destinations: Mapping[str, Destination]) -> Destination:
    """Return the destination with the given name or code."""
    key = text.strip().lower()
    for destination in destinations.values():
        if key in (destination.name.lower(), destination.code.lower()):
            return destination
    names = ", ".join(destination.name for destination in destinations.values())
    raise InvalidRequestError(f"unknown destination: {text!r}. Known destinations: {names}")


def build_request(
    destinations: Mapping[str, Destination],
    destination: str,
    date_from: str,
    date_to: str,
    depart: str,
    passengers: int = 1,
    budget: float | None = None,
    max_transfers: int | None = None,
    max_duration: int | None = None,
    changeable: bool = False,
    refundable: bool = False,
    min_luggage: int | None = None,
) -> TravelRequest:
    """Turn what the traveller typed into a checked TravelRequest."""
    try:
        first_day = parse_date(date_from, "first travel date")
        last_day = parse_date(date_to, "last travel date")
    except InvalidRecordError as error:
        raise InvalidRequestError(error.reason) from None
    return TravelRequest(
        destination=find_destination(destination, destinations),
        date_from=first_day,
        date_to=last_day,
        preferred_departure=TimeBand.from_text(depart),
        passengers=passengers,
        budget=budget,
        max_transfers=max_transfers,
        max_duration_minutes=max_duration,
        require_changeable=changeable,
        require_refundable=refundable,
        min_luggage_kg=min_luggage,
    )


def request_from_args(args: argparse.Namespace, session: Session) -> TravelRequest:
    """Build the request from the command-line options."""
    return build_request(
        session.data.destinations, args.destination, args.date_from, args.date_to, args.depart,
        passengers=args.passengers, budget=args.budget, max_transfers=args.max_transfers,
        max_duration=args.max_duration, changeable=args.changeable,
        refundable=args.refundable, min_luggage=args.min_luggage,
    )


def find_profile(key: str, profiles: Mapping[str, TravellerProfile]) -> TravellerProfile:
    """Return the profile with the given key."""
    if key not in profiles:
        raise InvalidRequestError(f"unknown profile: {key!r}. Known profiles: {', '.join(profiles)}")
    return profiles[key]


def emit(lines: Sequence[str], save: Path | None = None) -> None:
    """Print the lines, and also write them to a file when asked."""
    text = "\n".join(lines)
    print(text)
    if save is None:
        return
    try:
        save.parent.mkdir(parents=True, exist_ok=True)
        save.write_text(text + "\n", encoding="utf-8")
    except OSError as error:
        raise DataFileError(f"cannot write {save}: {error}") from error
    print(f"\nSaved to {save}")


def run_validate(args: argparse.Namespace) -> int:
    """Print what was accepted and what was set aside."""
    data = load_all(args.data)
    write_rejected(data.rejected, args.output / REJECTED_FILE)
    print("Data check")
    print(f"  Companies:    {len(data.companies)} accepted")
    print(f"  Destinations: {len(data.destinations)} accepted")
    print(f"  Departures:   {len(data.departures)} accepted "
          f"({len(data.sold_out())} sold out, kept and hidden from searches)")
    print(f"  Fares:        {len(data.fares)} accepted")
    print(f"  Rejected rows: {len(data.rejected)} (saved in {args.output / REJECTED_FILE})")
    for record in data.rejected:
        print(f"    {record.describe()}")
    return EXIT_OK


def run_search(args: argparse.Namespace) -> int:
    """Filter and rank the offers for one request and one set of weights."""
    session = open_session(args)
    request = request_from_args(args, session)
    if args.weights is not None:
        weights = check_weights(parse_weights(args.weights))
        source = "typed with --weights"
    else:
        profile = find_profile(args.profile, session.profiles)
        weights = check_weights(profile.weights)
        source = f"profile '{profile.key}' ({profile.description})"
    seats_left = session.ledger.all_seats_left()
    result = filter_offers(session.data.offers(), request, seats_left)
    ranked = rank_offers(result.accepted, request, weights)
    lines = format_request(request)
    lines += format_preferences(request, weights, source)
    lines += format_filter(result)
    if args.show_removed and result.removed:
        lines += format_removed(result)
    lines += format_ranking(ranked, weights, seats_left, args.top)
    emit(lines, args.save)
    logger.info(
        "Search: %s, %s to %s, weights from %s: %d in scope, %d removed, %d ranked, top offer %s",
        request.destination.name, request.date_from, request.date_to, source,
        result.in_scope, len(result.removed), len(ranked),
        ranked[0].offer.offer_id if ranked else "none",
    )
    return EXIT_OK


def run_compare(args: argparse.Namespace) -> int:
    """Rank the same offers under every profile and show the top offers side by side."""
    session = open_session(args)
    request = request_from_args(args, session)
    keys = args.profiles or list(session.profiles)
    seats_left = session.ledger.all_seats_left()
    result = filter_offers(session.data.offers(), request, seats_left)
    results = {}
    for key in keys:
        profile = find_profile(key, session.profiles)
        weights = check_weights(profile.weights)
        results[key] = (profile.description, weights, rank_offers(result.accepted, request, weights))
    lines = format_request(request)
    lines += ["", f"Preferred departure: {request.preferred_departure.describe()}"]
    lines += format_filter(result)
    if result.accepted:
        lines += format_comparison(results, seats_left)
    emit(lines, args.save)
    logger.info(
        "Compare: %s, %s to %s, profiles %s: %d ranked, top offers %s",
        request.destination.name, request.date_from, request.date_to, ", ".join(keys),
        len(result.accepted),
        ", ".join(f"{key}={ranked[0].offer.offer_id}" for key, (_d, _w, ranked) in results.items() if ranked)
        or "none",
    )
    return EXIT_OK


COMMANDS = {
    "validate": run_validate,
    "search": run_search,
    "compare": run_compare,
}


def _add_folder_options(parser: argparse.ArgumentParser, top_level: bool) -> None:
    """Add the folder options, so they work before or after the command name."""

    def default(value: Path) -> Path | str:
        return value if top_level else argparse.SUPPRESS

    parser.add_argument("--data", type=Path, default=default(DEFAULT_DATA_DIR),
                        help="folder with the data files (default: data)")
    parser.add_argument("--state", type=Path, default=default(DEFAULT_STATE_DIR),
                        help="folder where bookings are saved (default: state)")
    parser.add_argument("--logs", type=Path, default=default(DEFAULT_LOG_DIR),
                        help="folder for the log file (default: logs)")
    parser.add_argument("--output", type=Path, default=default(DEFAULT_OUTPUT_DIR),
                        help="folder for report files (default: output)")


def _add_request_options(parser: argparse.ArgumentParser) -> None:
    """Add the options that describe a travel request."""
    parser.add_argument("--destination", required=True, help="city name or code, for example Malaga")
    parser.add_argument("--date-from", required=True, help="first travel date, for example 2027-07-12")
    parser.add_argument("--date-to", required=True, help="last travel date, for example 2027-07-14")
    parser.add_argument("--depart", required=True,
                        help="preferred departure: morning, afternoon, evening, night or a time like 08:30")
    parser.add_argument("--passengers", type=int, default=1, help="number of passengers (default: 1)")
    parser.add_argument("--budget", type=float, help="mandatory: highest price per person in EUR")
    parser.add_argument("--max-transfers", type=int, help="mandatory: highest number of transfers")
    parser.add_argument("--max-duration", type=int, help="mandatory: longest journey in minutes")
    parser.add_argument("--changeable", action="store_true", help="mandatory: ticket must be changeable")
    parser.add_argument("--refundable", action="store_true", help="mandatory: ticket must be refundable")
    parser.add_argument("--min-luggage", type=int, help="mandatory: lowest luggage allowance in kg")
    parser.add_argument("--save", type=Path, help="also write the result to this file")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the program."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="BusBidder Spain: filter, rank and book simulated bus offers.",
    )
    parser.add_argument("--version", action="version", version=f"BusBidder Spain {__version__}")
    _add_folder_options(parser, top_level=True)
    commands = parser.add_subparsers(dest="command", metavar="command")

    validate = commands.add_parser("validate", help="check the data files and list rejected rows")
    _add_folder_options(validate, top_level=False)

    search = commands.add_parser("search", help="filter and rank offers for one traveller")
    _add_folder_options(search, top_level=False)
    _add_request_options(search)
    weights = search.add_mutually_exclusive_group(required=True)
    weights.add_argument("--profile", help="traveller profile from profiles.json, for example budget")
    weights.add_argument("--weights", help="own weights, for example price=40,duration=25,time=15,flex=20")
    search.add_argument("--top", type=int, default=DEFAULT_TOP,
                        help=f"how many ranked offers to show, 0 for all (default: {DEFAULT_TOP})")
    search.add_argument("--show-removed", action="store_true",
                        help="list every removed offer with the reasons")

    compare = commands.add_parser("compare", help="rank the same offers under several profiles")
    _add_folder_options(compare, top_level=False)
    _add_request_options(compare)
    compare.add_argument("--profiles", nargs="+", metavar="PROFILE",
                         help="profiles to compare (default: all)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the program and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    log = configure_logging(args.logs)
    try:
        log.info("Command: %s", args.command or "(none)")
        if args.command is None:
            parser.print_help()
            return EXIT_OK
        return COMMANDS[args.command](args)
    except BusBidderError as error:
        log.error("%s", error)
        print(f"Error: {error}")
        return EXIT_ERROR
    finally:
        close_logging()
