"""Command-line interface: the only module that prints."""

from __future__ import annotations

import argparse
import logging
import math
import re
import unicodedata
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
    format_next_step,
    format_preferences,
    format_ranking,
    format_removed,
    format_request,
    format_seats_after,
)
from busbidder.exceptions import (
    BusBidderError,
    DataFileError,
    InvalidRecordError,
    InvalidRequestError,
    NoSeatsError,
    WeightError,
)
from busbidder.loader import (
    PROFILES_FILE,
    REJECTED_FILE,
    LoadResult,
    load_all,
    load_profiles,
    write_rejected,
)
from busbidder.logsetup import LOG_FILE_NAME, close_logging, configure_logging
from busbidder.models import Destination, TimeBand, TravellerProfile, TravelRequest, count_of
from busbidder.ranking import (
    ScoredOffer,
    check_weights,
    describe_dominance,
    parse_weights,
    rank_offers,
    require_intended_dominance,
)
from busbidder.validation import parse_date

logger = logging.getLogger("busbidder.cli")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
PLAIN_INTEGER = re.compile(r"-?\d+")
DEFAULT_DATA_DIR = Path("data")
DEFAULT_STATE_DIR = Path("state")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_OUTPUT_DIR = Path("output")
SHOW_ALL = 0
INVALID_SUFFIX = "_invalid.csv"
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


def open_session(args: argparse.Namespace, need_profiles: bool = True) -> Session:
    """Read the data files and the saved bookings, and the profiles when needed."""
    data = load_all(args.data)
    write_rejected(data.rejected, args.output / REJECTED_FILE)
    # Booking commands do not use profiles, so a broken profiles file must
    # not stop a traveller from cancelling a booking.
    profiles = load_profiles(args.data / PROFILES_FILE) if need_profiles else {}
    store = BookingStore(args.state / BOOKINGS_FILE)
    ledger = SeatLedger(data.departures, store.load())
    return Session(data, profiles, store, ledger)


def plain(text: str) -> str:
    """Return text in lower case without accents, so 'Málaga' matches 'Malaga'."""
    decomposed = unicodedata.normalize("NFKD", text.strip())
    return "".join(letter for letter in decomposed if not unicodedata.combining(letter)).casefold()


def find_destination(text: str, destinations: Mapping[str, Destination]) -> Destination:
    """Return the destination with the given name or code."""
    key = plain(text)
    for destination in destinations.values():
        if key in (plain(destination.name), plain(destination.code)):
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
        reason = error.strerror or str(error)
        raise DataFileError(
            f"the result is shown above, but it could not be saved to {save}: {reason}"
        ) from error
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
    profiles = load_profiles(args.data / PROFILES_FILE)
    problems = []
    for profile in profiles.values():
        try:
            check_weights(profile.weights)
        except WeightError as error:
            problems.append(f"profile '{profile.key}': {error}")
    print(f"  Profiles:     {len(profiles) - len(problems)} valid, {len(problems)} invalid")
    for problem in problems:
        logger.error("%s: %s", PROFILES_FILE, problem)
        print(f"    {problem}")
    # The files of invalid examples are expected to be rejected. A rejected
    # row in a main file means the real data has a fault.
    in_main_files = [record for record in data.rejected if not record.source.endswith(INVALID_SUFFIX)]
    if in_main_files:
        logger.error("%d row(s) of the main data files were rejected", len(in_main_files))
        print(f"  Result: {count_of(len(in_main_files), 'row')} of the main data files "
              f"{'was' if len(in_main_files) == 1 else 'were'} rejected.")
    elif problems:
        print(f"  Result: {count_of(len(problems), 'profile')} in {PROFILES_FILE} "
              f"{'is' if len(problems) == 1 else 'are'} invalid.")
    else:
        print("  Result: the main data files and the profiles are valid.")
    return EXIT_ERROR if problems or in_main_files else EXIT_OK


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
    top: int = SHOW_ALL,
    show_removed: bool = False,
) -> tuple[list[str], list[ScoredOffer]]:
    """Filter and rank the offers; return the text to show and the ranking."""
    seats_left = session.ledger.all_seats_left()
    result = filter_offers(session.data.offers(), request, seats_left)
    ranked = rank_offers(result.accepted, request, weights)
    lines = format_request(request)
    lines += format_preferences(request, weights, source)
    lines += format_filter(result, request, session.data.travel_days())
    if show_removed and result.removed:
        lines += format_removed(result)
    lines += format_ranking(ranked, weights, seats_left, top, request.passengers)
    logger.info(
        "Search: %s, %s to %s, weights from %s: %d in scope, %d removed, %d ranked, top offer %s",
        request.destination.name, request.date_from, request.date_to, source,
        result.in_scope, len(result.removed), len(ranked),
        ranked[0].offer.offer_id if ranked else "none",
    )
    return lines, ranked


def run_search(args: argparse.Namespace) -> int:
    """Filter and rank the offers for one request and one set of weights."""
    # Typed weights do not use the profiles file, so it need not be there.
    session = open_session(args, need_profiles=args.weights is None)
    request = request_from_args(args, session)
    if args.weights is not None:
        weights, source = weights_from_text(args.weights)
    else:
        weights, source = weights_from_profile(args.profile, session)
    require_intended_dominance(weights, args.allow_dominant)
    lines, ranked = search_report(session, request, weights, source, args.top, args.show_removed)
    emit(lines + format_next_step(ranked, request.passengers), args.save)
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
        require_intended_dominance(weights, args.allow_dominant)
        results[key] = (profile.description, weights, rank_offers(result.accepted, request, weights))
    lines = format_request(request)
    lines += ["", f"Preferred departure: {request.preferred_departure.describe()}"]
    lines += format_filter(result, request, session.data.travel_days())
    if result.accepted:
        lines += format_comparison(results, seats_left, request.passengers)
        lines += [
            "",
            "Next step: to see the full ranking for one profile, use the search command "
            "with --profile. To book, use the book command with an offer ID.",
        ]
    logger.info(
        "Compare: %s, %s to %s, profiles %s: %d ranked, top offers %s",
        request.destination.name, request.date_from, request.date_to, ", ".join(keys),
        len(result.accepted),
        ", ".join(f"{key}={ranked[0].offer.offer_id}" for key, (_d, _w, ranked) in results.items() if ranked)
        or "none",
    )
    emit(lines, args.save)
    return EXIT_OK


def make_service(session: Session) -> BookingService:
    """Return the booking service for the loaded data."""
    return BookingService(session.store, session.data.departures, session.data.fares)


def seats_line(session: Session, service: BookingService, departure_id: str) -> str:
    """Return the line that says how many seats a departure has left."""
    if departure_id not in session.data.departures:
        return f"Departure {departure_id} is no longer in the data files."
    return format_seats_after(service.seats_left(departure_id))


def run_book(args: argparse.Namespace) -> int:
    """Book one offer and print the confirmation."""
    session = open_session(args, need_profiles=False)
    service = make_service(session)
    booking = service.book(args.offer, args.passengers)
    emit([*format_confirmation(booking), seats_line(session, service, booking.departure_id)])
    return EXIT_OK


def run_cancel(args: argparse.Namespace) -> int:
    """Cancel one booking and print what happened to it."""
    session = open_session(args, need_profiles=False)
    service = make_service(session)
    booking = service.cancel(args.ref)
    emit([*format_confirmation(booking), seats_line(session, service, booking.departure_id)])
    return EXIT_OK


def run_bookings(args: argparse.Namespace) -> int:
    """List the saved bookings."""
    emit(format_bookings(make_service(open_session(args, need_profiles=False)).bookings()))
    return EXIT_OK


def ask(read: Reader, prompt: str, convert: Callable[[str], object]):
    """Ask one question until the answer can be used."""
    while True:
        answer = read(prompt).strip()
        try:
            return convert(answer)
        except (BusBidderError, ValueError) as error:
            logger.info("Guided mode: answer to %r was not usable: %s", prompt.strip(), error)
            print(f"  {error}. Please answer again.")


def optional(convert: Callable[[str], object]) -> Callable[[str], object]:
    """Wrap a converter so that an empty answer means 'no value'."""
    return lambda text: None if not text else convert(text)


def to_number(text: str, kind: type, minimum: float, what: str):
    """Return the answer as a number of the given kind, at least minimum."""
    try:
        if "_" in text:
            raise ValueError
        number = kind(text)
    except ValueError:
        raise ValueError(f"{what} must be a number") from None
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a number")
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

    while True:
        chosen = ask(read, "Profile: ", choose)
        if chosen is None:
            chosen = ask(read, "Weights, for example price=40,duration=25,time=15,flex=20: ",
                         weights_from_text)
        dominance = describe_dominance(chosen[0])
        if dominance is None or ask(read, f"{dominance}. Is that what you want? (y/N): ", to_yes_no):
            return chosen
        print("Choose again.")


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
            print("Nothing to book. Start again with other dates or fewer requirements.")
            return EXIT_OK
        shown = len(ranked)

        def rank_from(text: str) -> int:
            rank = to_number(text, int, 1, "rank number")
            if rank > shown:
                raise ValueError(f"rank number must be at most {shown}")
            return rank

        rank = ask(read, f"\nRank number to book (1 to {shown}), or Enter to stop: ", optional(rank_from))
        if rank is None:
            print("Nothing booked.")
            return EXIT_OK
        offer = ranked[rank - 1].offer
        question = (
            f"Book {offer.offer_id} for {count_of(request.passengers, 'passenger')}, "
            f"{offer.price * request.passengers:.2f} EUR in total? (y/N): "
        )
        if not ask(read, question, to_yes_no):
            print("Nothing booked.")
            return EXIT_OK
        service = make_service(session)
        booking = service.book(offer.offer_id, request.passengers)
        emit([*format_confirmation(booking), seats_line(session, service, booking.departure_id)])
        return EXIT_OK
    except (EOFError, KeyboardInterrupt):
        logger.info("Guided mode stopped by the user")
        print("\nStopped. Nothing was booked.")
        return EXIT_OK


COMMANDS = {
    "validate": run_validate,
    "search": run_search,
    "compare": run_compare,
    "book": run_book,
    "bookings": run_bookings,
    "cancel": run_cancel,
}


class FriendlyParser(argparse.ArgumentParser):
    """An argument parser that reports a mistake in one line.

    The standard parser prints its whole usage text before the reason,
    which hides the reason from the traveller.
    """

    def error(self, message: str) -> None:
        """Print the reason and where to find the options, then stop with code 2."""
        self.exit(EXIT_USAGE, f"Error: {message}.\nRun 'python3 {self.prog} --help' to see the options.\n")


def whole_number(minimum: int | None = None) -> Callable[[str], int]:
    """Return a converter for a whole number, at least minimum when one is given.

    Python's int() also accepts forms such as 1_0, which a traveller would
    only type by mistake, so the text must consist of digits.
    """

    def convert(text: str) -> int:
        if not PLAIN_INTEGER.fullmatch(text.strip()):
            raise argparse.ArgumentTypeError(f"must be a whole number: {text!r}")
        number = int(text)
        if minimum is not None and number < minimum:
            raise argparse.ArgumentTypeError(f"must be {minimum} or more: {text!r}")
        return number

    return convert


def amount(text: str) -> float:
    """Return an amount typed on the command line as a number."""
    if "_" in text:
        raise argparse.ArgumentTypeError(f"must be a number: {text!r}")
    try:
        return float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a number: {text!r}") from None


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
    parser.add_argument("--passengers", type=whole_number(), default=1, help="number of passengers (default: 1)")
    parser.add_argument("--budget", type=amount, help="mandatory: highest price per person in EUR")
    parser.add_argument("--max-transfers", type=whole_number(), help="mandatory: highest number of transfers")
    parser.add_argument("--max-duration", type=whole_number(), help="mandatory: longest journey in minutes")
    parser.add_argument("--changeable", action="store_true", help="mandatory: ticket must be changeable")
    parser.add_argument("--refundable", action="store_true", help="mandatory: ticket must be refundable")
    parser.add_argument("--min-luggage", type=whole_number(), help="mandatory: lowest luggage allowance in kg")
    parser.add_argument("--allow-dominant", action="store_true",
                        help="accept weights where one criterion counts for more than 70%%")
    parser.add_argument("--save", type=Path, help="also write the result to this file")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the program."""
    parser = FriendlyParser(
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
    search.add_argument("--top", type=whole_number(0), default=SHOW_ALL,
                        help="show only the first N ranked offers (default: all)")
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
    book.add_argument("--passengers", type=whole_number(), default=1, help="number of passengers (default: 1)")

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
    command = args.command or "guided mode"
    try:
        log = configure_logging(args.logs)
    except OSError as error:
        print(f"Error: cannot open the log file in {args.logs}: {error}")
        return EXIT_ERROR
    try:
        log.info("Command: %s", command)
        if args.command is None:
            return run_guided(args)
        return COMMANDS[args.command](args)
    except NoSeatsError as error:
        # A full bus is not a mistake by the traveller, so it is not called an error.
        log.info("%s refused: %s", command, error)
        print(f"Not booked: {error}")
        return EXIT_ERROR
    except BusBidderError as error:
        log.error("%s failed: %s: %s", command, type(error).__name__, error)
        print(f"Error: {error}")
        return EXIT_ERROR
    except Exception:
        # Last resort for a fault in the program itself. It is logged with
        # its traceback, so it is never lost, and the user gets one line.
        log.exception("Unexpected error in %s", command)
        print(f"Error: unexpected problem. The details are in {args.logs / LOG_FILE_NAME}")
        return EXIT_ERROR
    finally:
        close_logging()
