"""Command-line interface: the only module that prints."""

from __future__ import annotations

import argparse
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from busbidder import __version__
from busbidder.booking import BOOKINGS_FILE, BookingService, BookingStore, SeatLedger
from busbidder.constraints import filter_offers
from busbidder.display import (
    format_bookings,
    format_comparison,
    format_confirmation,
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
from busbidder.ranking import ScoredOffer, check_weights, parse_weights, rank_offers
from busbidder.validation import parse_date

logger = logging.getLogger("busbidder.cli")

EXIT_OK = 0
EXIT_ERROR = 1
DEFAULT_DATA_DIR = Path("data")
DEFAULT_STATE_DIR = Path("state")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_OUTPUT_DIR = Path("output")
DEFAULT_TOP = 10
CUSTOM_WEIGHTS = "custom"

# The guided mode reads answers through a function like input(), so tests
# can supply the answers.
Reader = Callable[[str], str]


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


def weights_from_profile(key: str, session: Session) -> tuple[dict[str, float], str]:
    """Return the checked weights of a profile and a text saying where they come from."""
    profile = find_profile(key, session.profiles)
    return check_weights(profile.weights), f"profile '{profile.key}' ({profile.description})"


def weights_from_text(text: str) -> tuple[dict[str, float], str]:
    """Return checked weights typed by the traveller."""
    return check_weights(parse_weights(text)), "typed by the traveller"


def search_report(
    session: Session,
    request: TravelRequest,
    weights: Mapping[str, float],
    source: str,
    top: int = DEFAULT_TOP,
    show_removed: bool = False,
) -> tuple[list[str], list[ScoredOffer]]:
    """Filter and rank the offers; return the text to show and the ranking."""
    seats_left = session.ledger.all_seats_left()
    result = filter_offers(session.data.offers(), request, seats_left)
    ranked = rank_offers(result.accepted, request, weights)
    lines = format_request(request)
    lines += format_preferences(request, weights, source)
    lines += format_filter(result)
    if show_removed and result.removed:
        lines += format_removed(result)
    lines += format_ranking(ranked, weights, seats_left, top)
    logger.info(
        "Search: %s, %s to %s, weights from %s: %d in scope, %d removed, %d ranked, top offer %s",
        request.destination.name, request.date_from, request.date_to, source,
        result.in_scope, len(result.removed), len(ranked),
        ranked[0].offer.offer_id if ranked else "none",
    )
    return lines, ranked


def run_search(args: argparse.Namespace) -> int:
    """Filter and rank the offers for one request and one set of weights."""
    session = open_session(args)
    request = request_from_args(args, session)
    if args.weights is not None:
        weights, source = weights_from_text(args.weights)
    else:
        weights, source = weights_from_profile(args.profile, session)
    lines, _ranked = search_report(session, request, weights, source, args.top, args.show_removed)
    emit(lines, args.save)
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


def make_service(session: Session) -> BookingService:
    """Return the booking service for the loaded data."""
    return BookingService(session.store, session.data.departures, session.data.fares)


def run_book(args: argparse.Namespace) -> int:
    """Book one offer and print the confirmation."""
    session = open_session(args)
    service = make_service(session)
    booking = service.book(args.offer, args.passengers)
    lines = format_confirmation(booking)
    lines.append(
        f"Seats left on departure {booking.departure_id}: {service.seats_left(booking.departure_id)}"
    )
    emit(lines)
    return EXIT_OK


def run_cancel(args: argparse.Namespace) -> int:
    """Cancel one booking and print what happened to it."""
    session = open_session(args)
    service = make_service(session)
    booking = service.cancel(args.ref)
    lines = format_confirmation(booking)
    lines.append(
        f"Seats left on departure {booking.departure_id}: {service.seats_left(booking.departure_id)}"
    )
    emit(lines)
    return EXIT_OK


def run_bookings(args: argparse.Namespace) -> int:
    """List the saved bookings."""
    emit(format_bookings(make_service(open_session(args)).bookings()))
    return EXIT_OK


def ask(read: Reader, prompt: str, convert: Callable[[str], object]):
    """Ask one question until the answer can be used."""
    while True:
        answer = read(prompt).strip()
        try:
            return convert(answer)
        except (BusBidderError, ValueError) as error:
            print(f"  {error}. Please answer again.")


def optional(convert: Callable[[str], object]) -> Callable[[str], object]:
    """Wrap a converter so that an empty answer means 'no value'."""
    return lambda text: None if not text else convert(text)


def to_number(text: str, kind: type, minimum: float, what: str):
    """Return the answer as a number of the given kind, at least minimum."""
    try:
        number = kind(text)
    except ValueError:
        raise ValueError(f"{what} must be a number") from None
    if number < minimum:
        raise ValueError(f"{what} must be at least {minimum}")
    return number


def to_yes_no(text: str) -> bool:
    """Return True for y or yes, False for n, no or an empty answer."""
    answer = text.lower()
    if answer in ("y", "yes"):
        return True
    if answer in ("", "n", "no"):
        return False
    raise ValueError("answer y or n")


def ask_request(session: Session, read: Reader) -> TravelRequest:
    """Ask for the journey and the mandatory conditions, one question at a time."""
    destination = ask(read, "Destination: ",
                      lambda text: find_destination(text, session.data.destinations))
    first_day = ask(read, "First travel date (YYYY-MM-DD): ",
                    lambda text: parse_date(text, "first travel date"))

    def last_day_from(text: str):
        day = parse_date(text, "last travel date")
        if day < first_day:
            raise ValueError("last travel date is before the first travel date")
        return day

    last_day = ask(read, "Last travel date (YYYY-MM-DD): ", last_day_from)
    passengers = ask(read, "Passengers [1]: ",
                     lambda text: 1 if not text else to_number(text, int, 1, "passengers"))
    preferred = ask(read, "Preferred departure (morning, afternoon, evening, night or HH:MM): ",
                    TimeBand.from_text)
    print("Mandatory conditions: an offer that fails one is removed. Press Enter to skip one.")
    budget = ask(read, "  Highest price per person in EUR: ",
                 optional(lambda text: to_number(text, float, 0.01, "budget")))
    max_transfers = ask(read, "  Highest number of transfers: ",
                        optional(lambda text: to_number(text, int, 0, "transfers")))
    max_duration = ask(read, "  Longest journey in minutes: ",
                       optional(lambda text: to_number(text, int, 1, "journey time")))
    changeable = ask(read, "  Ticket must be changeable? (y/N): ", to_yes_no)
    refundable = ask(read, "  Ticket must be refundable? (y/N): ", to_yes_no)
    min_luggage = ask(read, "  Lowest luggage allowance in kg: ",
                      optional(lambda text: to_number(text, int, 0, "luggage")))
    return TravelRequest(
        destination=destination, date_from=first_day, date_to=last_day,
        preferred_departure=preferred, passengers=passengers, budget=budget,
        max_transfers=max_transfers, max_duration_minutes=max_duration,
        require_changeable=changeable, require_refundable=refundable,
        min_luggage_kg=min_luggage,
    )


def ask_weights(session: Session, read: Reader) -> tuple[dict[str, float], str]:
    """Ask for a profile, or for the traveller's own weights."""
    print("Preferences: how much each criterion counts. Choose a profile:")
    for profile in session.profiles.values():
        print(f"  {profile.key}: {profile.description}")
    print(f"  {CUSTOM_WEIGHTS}: type your own weights")

    def choose(text: str):
        if text.lower() == CUSTOM_WEIGHTS:
            return None
        return weights_from_profile(text, session)

    chosen = ask(read, "Profile: ", choose)
    if chosen is not None:
        return chosen
    return ask(read, "Weights, for example price=40,duration=25,time=15,flex=20: ", weights_from_text)


def run_guided(args: argparse.Namespace, read: Reader | None = None) -> int:
    """Ask for a request, show the ranking and offer to book one of the offers."""
    # input is looked up here, not in the signature, so a test can replace it.
    read = read or input
    session = open_session(args)
    print("BusBidder Spain: guided search for a bus from Madrid. Press Ctrl+C to stop.")
    names = ", ".join(destination.name for destination in session.data.destinations.values())
    print(f"Destinations: {names}")
    try:
        request = ask_request(session, read)
        weights, source = ask_weights(session, read)
        print()
        lines, ranked = search_report(session, request, weights, source)
        emit(lines)
        if not ranked:
            return EXIT_OK
        shown = min(len(ranked), DEFAULT_TOP)

        def rank_from(text: str) -> int:
            rank = to_number(text, int, 1, "rank number")
            if rank > shown:
                raise ValueError(f"rank number must be at most {shown}")
            return rank

        rank = ask(read, f"\nRank number to book (1 to {shown}), or Enter to stop: ", optional(rank_from))
        if rank is None:
            print("Nothing booked.")
            return EXIT_OK
        booking = make_service(session).book(ranked[rank - 1].offer.offer_id, request.passengers)
        emit(format_confirmation(booking))
        return EXIT_OK
    except (EOFError, KeyboardInterrupt):
        print("\nStopped.")
        return EXIT_OK


COMMANDS = {
    "validate": run_validate,
    "search": run_search,
    "compare": run_compare,
    "book": run_book,
    "bookings": run_bookings,
    "cancel": run_cancel,
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
        description="BusBidder Spain: filter, rank and book simulated bus offers. "
                    "Run without a command for the guided mode.",
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

    book = commands.add_parser("book", help="book an offer (simulated, no payment)")
    _add_folder_options(book, top_level=False)
    book.add_argument("--offer", required=True, help="offer ID from a ranking, for example SR-MAL-0001-B")
    book.add_argument("--passengers", type=int, default=1, help="number of passengers (default: 1)")

    bookings = commands.add_parser("bookings", help="list the saved bookings")
    _add_folder_options(bookings, top_level=False)

    cancel = commands.add_parser("cancel", help="cancel a booking (simulated refund)")
    _add_folder_options(cancel, top_level=False)
    cancel.add_argument("--ref", required=True, help="booking reference, for example BB-20261120-7K3QX9")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the program and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    log = configure_logging(args.logs)
    try:
        log.info("Command: %s", args.command or "(none)")
        if args.command is None:
            return run_guided(args)
        return COMMANDS[args.command](args)
    except BusBidderError as error:
        log.error("%s", error)
        print(f"Error: {error}")
        return EXIT_ERROR
    finally:
        close_logging()
