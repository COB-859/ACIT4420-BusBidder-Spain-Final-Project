"""Exploratory tests: use the program as a traveller would and report what it says.

    python3 tools/exploratory_tests.py edge     unusual input, with the feedback given
    python3 tools/exploratory_tests.py rules    rules that must hold for any input
    python3 tools/exploratory_tests.py race     several bookings at the same moment
    python3 tools/exploratory_tests.py trials   how stable and how even-handed the ranking is
    python3 tools/exploratory_tests.py alternatives   design choices measured against alternatives

Run it from the project folder. It reads the real data files, and writes
bookings and logs only to a temporary folder, so the project is not changed.

The unit tests in tests/ check known answers. These checks look for what
the unit tests did not think of: input nobody planned for, rules that
must hold whatever the request is, and what the traveller is told. The
random parts use a fixed seed, so "edge", "rules", "trials" and
"alternatives" give the same result on every run. "race" depends on timing and can differ between
runs.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import random
import re
import statistics
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from busbidder import cli  # noqa: E402
from busbidder.booking import BookingService, BookingStore  # noqa: E402
from busbidder.constraints import filter_offers  # noqa: E402
from busbidder.exceptions import BusBidderError  # noqa: E402
from busbidder.loader import PROFILES_FILE, load_all, load_profiles  # noqa: E402
from busbidder.ranking import ScoredOffer, check_weights, near_tie_gap, rank_offers  # noqa: E402

SEED = 4420
DATA = ROOT / "data"
DATES = ["--date-from", "2027-07-12", "--date-to", "2027-07-14"]
MALAGA = ["--destination", "Malaga", *DATES, "--depart", "morning"]
BUDGET = ["--profile", "budget"]
# What the traveller reads first: an error, a note, or the result of the search.
FEEDBACK = re.compile(
    r"^(Error|Not booked|Run '|No buses|BusBidder has|Note: (some|the first)|\d+ of the \d+ offers|"
    r"The (cheapest|most|shortest|highest|offer with)|Without this|Changing this|"
    r"Offers left to rank|Booking |No refund|Refund:|This bus|Seats left|You have|\d+ bookings?:)"
)
# A booking reference changes on every run, so it is masked in the report.
REFERENCE = re.compile(r"BB-\d{8}-[A-Z0-9]{6}", re.IGNORECASE)


def run_program(arguments: list[str], folder: Path) -> tuple[object, str]:
    """Run one command inside this process; return its exit code and everything it printed."""
    folders = ["--data", str(DATA), "--state", str(folder / "state"),
               "--logs", str(folder / "logs"), "--output", str(folder / "output")]
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            code = cli.main([*folders, *arguments])
    except SystemExit as stop:
        code = stop.code
    except Exception as error:  # a crash is exactly what these tests look for
        code = f"CRASH {type(error).__name__}: {error}"
    return code, printed.getvalue()


# --------------------------------------------------------------------------
# edge: unusual input and the feedback the traveller gets
# --------------------------------------------------------------------------

EDGE_CASES: list[tuple[str, list[str]]] = [
    ("destination as a code", ["search", "--destination", "MAL", *DATES, "--depart", "morning", *BUDGET]),
    ("destination in lower case", ["search", "--destination", "malaga", *DATES, "--depart", "morning", *BUDGET]),
    ("destination with spaces around it", ["search", "--destination", " Malaga ", *DATES, "--depart", "morning", *BUDGET]),
    ("destination with an accent", ["search", "--destination", "Málaga", *DATES, "--depart", "morning", *BUDGET]),
    ("destination left empty", ["search", "--destination", "", *DATES, "--depart", "morning", *BUDGET]),
    ("unknown destination", ["search", "--destination", "Paris", *DATES, "--depart", "morning", *BUDGET]),
    ("date in the wrong format", ["search", "--destination", "Malaga", "--date-from", "12-07-2027", "--date-to", "2027-07-14", "--depart", "morning", *BUDGET]),
    ("date that does not exist", ["search", "--destination", "Malaga", "--date-from", "2027-02-30", "--date-to", "2027-03-01", "--depart", "morning", *BUDGET]),
    ("last date before the first", ["search", "--destination", "Malaga", "--date-from", "2027-07-14", "--date-to", "2027-07-12", "--depart", "morning", *BUDGET]),
    ("dates outside the data", ["search", "--destination", "Malaga", "--date-from", "2027-08-01", "--date-to", "2027-08-02", "--depart", "morning", *BUDGET]),
    ("dates partly outside the data", ["search", "--destination", "Malaga", "--date-from", "2027-07-16", "--date-to", "2027-07-20", "--depart", "morning", *BUDGET]),
    ("a single day", ["search", "--destination", "Malaga", "--date-from", "2027-07-12", "--date-to", "2027-07-12", "--depart", "morning", *BUDGET]),
    ("year 1 to year 9999", ["search", "--destination", "Malaga", "--date-from", "0001-01-01", "--date-to", "9999-12-31", "--depart", "morning", *BUDGET]),
    ("departure 'noon'", ["search", "--destination", "Malaga", *DATES, "--depart", "noon", *BUDGET]),
    ("departure 25:00", ["search", "--destination", "Malaga", *DATES, "--depart", "25:00", *BUDGET]),
    ("departure 8:30 without the zero", ["search", "--destination", "Malaga", *DATES, "--depart", "8:30", *BUDGET]),
    ("departure in capitals", ["search", "--destination", "Malaga", *DATES, "--depart", "MORNING", *BUDGET]),
    ("departure at midnight", ["search", "--destination", "Malaga", *DATES, "--depart", "00:00", *BUDGET]),
    ("0 passengers", ["search", *MALAGA, *BUDGET, "--passengers", "0"]),
    ("-1 passengers", ["search", *MALAGA, *BUDGET, "--passengers", "-1"]),
    ("passengers as a word", ["search", *MALAGA, *BUDGET, "--passengers", "two"]),
    ("1.5 passengers", ["search", *MALAGA, *BUDGET, "--passengers", "1.5"]),
    ("1000 passengers", ["search", *MALAGA, *BUDGET, "--passengers", "1000"]),
    ("budget 0", ["search", *MALAGA, *BUDGET, "--budget", "0"]),
    ("budget -5", ["search", *MALAGA, *BUDGET, "--budget", "-5"]),
    ("budget 'nan'", ["search", *MALAGA, *BUDGET, "--budget", "nan"]),
    ("budget 'inf'", ["search", *MALAGA, *BUDGET, "--budget", "inf"]),
    ("budget of 1 EUR", ["search", *MALAGA, *BUDGET, "--budget", "1"]),
    ("-1 transfers", ["search", *MALAGA, *BUDGET, "--max-transfers", "-1"]),
    ("longest journey 0 minutes", ["search", *MALAGA, *BUDGET, "--max-duration", "0"]),
    ("longest journey 60 minutes", ["search", *MALAGA, *BUDGET, "--max-duration", "60"]),
    ("no transfers and a budget of 30 EUR", ["search", *MALAGA, *BUDGET, "--max-transfers", "0", "--budget", "30"]),
    ("budget of 20 EUR and a changeable ticket", ["search", *MALAGA, *BUDGET, "--budget", "20", "--changeable"]),
    ("refundable ticket and a budget of 40 EUR", ["search", *MALAGA, *BUDGET, "--refundable", "--budget", "40"]),
    ("the specification's example request", ["search", *MALAGA, "--budget", "50", "--max-transfers", "1", "--changeable", "--weights", "price=40,duration=25,time=15,flex=20", "--top", "1"]),
    ("999 kg of luggage", ["search", *MALAGA, *BUDGET, "--min-luggage", "999"]),
    ("changeable ticket only, budget 30", ["search", *MALAGA, *BUDGET, "--changeable", "--budget", "30"]),
    ("every requirement at once", ["search", *MALAGA, *BUDGET, "--budget", "40", "--max-transfers", "0", "--max-duration", "400", "--changeable", "--refundable", "--min-luggage", "30"]),
    ("weights as fractions", ["search", *MALAGA, "--weights", "price=0.4,duration=0.25,time=0.15,flex=0.2"]),
    ("weights with percent signs", ["search", *MALAGA, "--weights", "price=40%,duration=25%,time=15%,flex=20%"]),
    ("weights with spaces", ["search", *MALAGA, "--weights", "price = 40, duration = 25, time = 15, flex = 20"]),
    ("weights that total 110", ["search", *MALAGA, "--weights", "price=50,duration=25,time=15,flex=20"]),
    ("a negative weight", ["search", *MALAGA, "--weights", "price=140,duration=-40,time=0,flex=0"]),
    ("only two weights", ["search", *MALAGA, "--weights", "price=50,duration=50"]),
    ("weights with a colon", ["search", *MALAGA, "--weights", "price:40,duration:25,time:15,flex:20"]),
    ("an unknown criterion", ["search", *MALAGA, "--weights", "price=40,duration=25,time=15,flex=10,comfort=10"]),
    ("a weight given twice", ["search", *MALAGA, "--weights", "price=20,price=20,duration=25,time=15,flex=20"]),
    ("one weight at 80%", ["search", *MALAGA, "--weights", "price=80,duration=10,time=5,flex=5"]),
    ("one weight at 80%, confirmed", ["search", *MALAGA, "--weights", "price=80,duration=10,time=5,flex=5", "--allow-dominant"]),
    ("profile and weights together", ["search", *MALAGA, *BUDGET, "--weights", "price=40,duration=25,time=15,flex=20"]),
    ("neither profile nor weights", ["search", *MALAGA]),
    ("unknown profile", ["search", *MALAGA, "--profile", "student"]),
    ("--top -3", ["search", *MALAGA, *BUDGET, "--top", "-3"]),
    ("--top 999", ["search", *MALAGA, *BUDGET, "--top", "999"]),
    ("save to a place that is not a folder", ["search", *MALAGA, *BUDGET, "--save", str(DATA / "companies.csv" / "result.txt")]),
    ("only the destination given", ["search", "--destination", "Malaga"]),
    ("compare with an unknown profile", ["compare", *MALAGA, "--profiles", "budget", "student"]),
    ("compare with a budget of 1 EUR", ["compare", *MALAGA, "--budget", "1"]),
    ("bookings before any booking", ["bookings"]),
    ("book 2 flex seats", ["book", "--offer", "CL-MAL-0007-F", "--passengers", "2"]),
    ("book with the offer ID in lower case", ["book", "--offer", "cl-mal-0007-s"]),
    ("book 0 passengers", ["book", "--offer", "CL-MAL-0007-S", "--passengers", "0"]),
    ("book 50 seats on a bus with fewer", ["book", "--offer", "CL-MAL-0007-S", "--passengers", "50"]),
    ("book the last 2 seats of a bus", ["book", "--offer", "SR-VAL-0002-S", "--passengers", "2"]),
    ("book that bus again", ["book", "--offer", "SR-VAL-0002-B"]),
    ("book a bus with no seats in the data", ["book", "--offer", "SR-COR-0001-B"]),
    ("book an unknown offer", ["book", "--offer", "XX"]),
    ("book a fare that bus does not sell", ["book", "--offer", "SR-VAL-0002-F"]),
    ("cancel a reference in the wrong format", ["cancel", "--ref", "hello"]),
    ("cancel a reference that does not exist", ["cancel", "--ref", "BB-20261120-AAAAAA"]),
    ("an unknown command", ["frobnicate"]),
]


def feedback_lines(text: str) -> list[str]:
    """Return the lines of the output that tell the traveller what happened."""
    lines = [line.strip() for line in text.splitlines() if FEEDBACK.match(line.strip())]
    return lines or [text.strip().splitlines()[-1] if text.strip() else "(nothing printed)"]


def explore_edge_cases() -> int:
    """Run every edge case and print the exit code and the feedback."""
    crashes = 0
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        cases = list(EDGE_CASES)
        references: list[str] = []
        number = 0
        while cases:
            label, arguments = cases.pop(0)
            number += 1
            code, text = run_program(arguments, folder)
            crashes += isinstance(code, str) or "unexpected problem" in text
            references += re.findall(r"Your reference is (BB-\d{8}-[A-Z0-9]{6})", text)
            # The project folder is masked before quoting, so the report is
            # the same wherever the project is kept.
            masked = [a.replace(str(ROOT), "<project>") for a in arguments]
            shown = " ".join(a if a and " " not in a else repr(a) for a in masked)
            shown = REFERENCE.sub("BB-<date>-<code>", shown)
            print(f"{number:>2}. {label}  [exit code {code}]")
            print(f"    command:  {shown}")
            for line in feedback_lines(text)[:5]:
                line = REFERENCE.sub("BB-<date>-<code>", line).replace(str(ROOT), "<project>")
                print(f"    feedback: {line[:230]}")
            if label == "book a fare that bus does not sell" and references:
                # The cancellations need references that exist only now.
                flex, standard = references[0], references[1]
                cases = [
                    ("cancel the 2 flex seats (90% refundable)", ["cancel", "--ref", flex]),
                    ("cancel the same booking again", ["cancel", "--ref", flex]),
                    ("cancel a standard seat, reference in lower case", ["cancel", "--ref", f" {standard.lower()} "]),
                    ("bookings after booking and cancelling", ["bookings"]),
                    *cases,
                ]
    print(f"\n{number} cases run. Crashes or unexpected faults: {crashes}")
    return crashes


# --------------------------------------------------------------------------
# rules: what must hold whatever the traveller asks for
# --------------------------------------------------------------------------

class Rules:
    """Counts how often each rule was checked and remembers the failures."""

    def __init__(self) -> None:
        self.checked: dict[str, int] = {}
        self.failed: list[tuple[str, str]] = []

    def check(self, rule: str, holds: bool, detail: str = "") -> None:
        self.checked[rule] = self.checked.get(rule, 0) + 1
        if not holds:
            self.failed.append((rule, detail))


def random_weights(rng: random.Random) -> dict[str, float]:
    """Return random weights that total 100, always using the four required criteria."""
    names = ["price", "duration", "time", "flex"]
    names += rng.sample(["transfers", "rating", "emissions"], rng.randint(0, 3))
    cuts = sorted(rng.sample(range(1, 100), len(names) - 1))
    parts = [end - start for start, end in zip([0, *cuts], [*cuts, 100])]
    return dict(zip(names, map(float, parts)))


def check_search_rules(rules: Rules, rng: random.Random, searches: int = 1000) -> None:
    """Run random searches and check the rules of filtering and ranking."""
    data = load_all(DATA)
    offers = data.offers()
    seats = {key: departure.seats for key, departure in data.departures.items()}
    cities = [destination.name for destination in data.destinations.values()]
    for _ in range(searches):
        first_day = rng.randint(10, 16)
        last_day = rng.randint(first_day, 16)
        wanted = {"destination": rng.choice(cities), "date_from": f"2027-07-{first_day}",
                  "date_to": f"2027-07-{last_day}",
                  "depart": rng.choice(["morning", "night", "14:00"]), "passengers": rng.randint(1, 6)}
        if rng.random() < 0.6:
            wanted["budget"] = rng.choice([20, 35, 50, 80])
        if rng.random() < 0.4:
            wanted["max_transfers"] = rng.choice([0, 1, 2])
        if rng.random() < 0.3:
            wanted["changeable"] = True
        if rng.random() < 0.2:
            wanted["refundable"] = True
        if rng.random() < 0.3:
            wanted["max_duration"] = rng.choice([300, 420, 540])
        if rng.random() < 0.3:
            wanted["min_luggage"] = rng.choice([10, 20, 30])
        request = cli.build_request(data.destinations, **wanted)
        result = filter_offers(offers, request, seats)
        weights = check_weights(random_weights(rng))
        ranked = rank_offers(result.accepted, request, weights)
        order = [scored.offer.offer_id for scored in ranked]

        for scored in ranked:
            offer = scored.offer
            meets = (
                offer.departure.destination.name == wanted["destination"]
                and wanted["date_from"] <= offer.departure.departure.date().isoformat() <= wanted["date_to"]
                and offer.duration_minutes <= wanted.get("max_duration", 10 ** 6)
                and offer.fare.luggage_kg >= wanted.get("min_luggage", 0)
                and seats[offer.departure.departure_id] >= wanted["passengers"]
                and offer.price <= wanted.get("budget", float("inf"))
                and offer.departure.transfers <= wanted.get("max_transfers", 99)
                and (offer.fare.changeable or not wanted.get("changeable"))
                and (offer.fare.refundable or not wanted.get("refundable"))
            )
            rules.check("every ranked offer meets every mandatory condition", meets, offer.offer_id)
            rules.check("criterion points are between 0 and 100",
                        all(0 <= score.points <= 100 for score in scored.scores.values()), offer.offer_id)
            rules.check("the total is the sum of the contributions",
                        abs(scored.total - sum(scored.contributions.values())) < 0.011, offer.offer_id)
            rules.check("the total is between 0 and 100", 0 <= scored.total <= 100.001, offer.offer_id)
        rules.check("no offer is lost: kept + removed = offers for the city and dates",
                    len(result.accepted) + len(result.removed) == result.in_scope)
        rules.check("the ranking is ordered best first",
                    all(first.total >= second.total for first, second in zip(ranked, ranked[1:])))

        shuffled = offers[:]
        rng.shuffle(shuffled)
        again = rank_offers(filter_offers(shuffled, request, seats).accepted, request, weights)
        rules.check("the ranking does not depend on the order in which the offers are given",
                    order == [scored.offer.offer_id for scored in again])
        fractions = check_weights({name: weight / 100 for name, weight in weights.items()})
        rules.check("weights as fractions rank like weights as percentages",
                    order == [s.offer.offer_id for s in rank_offers(result.accepted, request, fractions)])

        kept = {offer.offer_id for offer in result.accepted}
        if "budget" in wanted:
            richer = cli.build_request(data.destinations, **{**wanted, "budget": wanted["budget"] + 30})
            rules.check("a higher budget never removes an offer",
                        kept <= {offer.offer_id for offer in filter_offers(offers, richer, seats).accepted})
        larger = cli.build_request(data.destinations, **{**wanted, "passengers": wanted["passengers"] + 3})
        rules.check("more passengers never add an offer",
                    {offer.offer_id for offer in filter_offers(offers, larger, seats).accepted} <= kept)
        if len(ranked) > 1:
            even = check_weights({"price": 25.0, "duration": 25.0, "time": 25.0, "flex": 25.0})
            pricey = check_weights({"price": 55.0, "duration": 15.0, "time": 15.0, "flex": 15.0})
            before = rank_offers(result.accepted, request, even)[0].scores["price"].points
            after = rank_offers(result.accepted, request, pricey)[0].scores["price"].points
            rules.check("raising the price weight from 25% to 55% never picks a winner that is worse on price",
                        after >= before - 1e-9, f"{before} -> {after}")


def check_booking_rules(rules: Rules, rng: random.Random, steps: int = 600) -> None:
    """Book and cancel at random and compare the program with an independent count."""
    data = load_all(DATA)
    small_buses = [fare for fare in data.fares.values() if data.departures[fare.departure_id].seats <= 6][:25]
    with tempfile.TemporaryDirectory() as temp:
        path = Path(temp) / "bookings.json"
        service = BookingService(BookingStore(path), data.departures, data.fares)
        references: list[str] = []
        for _ in range(steps):
            before = path.read_text(encoding="utf-8") if path.exists() else None
            try:
                if references and rng.random() < 0.4:
                    booking = service.cancel(rng.choice(references))
                    expected = round(booking.total_price * booking.fare.refund_percent / 100, 2)
                    rules.check("the refund is the stored percentage of the price paid",
                                booking.refund_amount == expected, booking.reference)
                else:
                    booking = service.book(rng.choice(small_buses).offer_id, rng.randint(1, 4))
                    references.append(booking.reference)
                    rules.check("a booking reference has the right format",
                                re.fullmatch(r"BB-\d{8}-[A-Z0-9]{6}", booking.reference) is not None)
            except BusBidderError:
                after = path.read_text(encoding="utf-8") if path.exists() else None
                rules.check("a refused action changes nothing in the bookings file", after == before)
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
            held: dict[str, int] = {}
            for row in saved:
                if row["status"] == "confirmed":
                    bus = row["fare"]["departure_id"]
                    held[bus] = held.get(bus, 0) + row["passengers"]
            for bus, booked in held.items():
                capacity = data.departures[bus].seats
                rules.check("a bus never has more seats booked than it has", booked <= capacity, bus)
                rules.check("seats left = seats of the bus - seats of active bookings",
                            service.seats_left(bus) == capacity - booked, bus)
            rules.check("booking references are unique",
                        len({row["reference"] for row in saved}) == len(saved))


def check_random_input(rules: Rules, rng: random.Random, lines: int = 1500) -> None:
    """Feed random words to the command line: it may refuse them, but must not crash."""
    words = ["search", "compare", "book", "cancel", "bookings", "validate", "--destination",
             "--date-from", "--date-to", "--depart", "--profile", "--weights", "--budget",
             "--passengers", "--offer", "--ref", "--top", "--max-transfers", "--changeable",
             "--save", "Malaga", "2027-07-12", "morning", "budget", "-1", "0", "1e309", "nan", "",
             "  ", "price=40,duration=25,time=15,flex=20", "price==,", "BB-20261120-AAAAAA",
             "CL-MAL-0007-S", "../../etc", "%s%s", "9" * 40, "2027-13-01", "24:00", "--allow-dominant"]
    with tempfile.TemporaryDirectory() as temp:
        for _ in range(lines):
            arguments = [rng.choice(words[:6]), *(rng.choice(words) for _ in range(rng.randint(0, 8)))]
            code, text = run_program(arguments, Path(temp))
            crashed = isinstance(code, str) or "unexpected problem" in text
            rules.check("random words on the command line never crash the program",
                        not crashed, " ".join(arguments))


def explore_rules() -> int:
    """Check every rule and print how often it was checked and whether it held."""
    rules = Rules()
    rng = random.Random(SEED)
    check_search_rules(rules, rng)
    check_booking_rules(rules, rng)
    check_random_input(rules, rng)
    failed_rules = {rule for rule, _detail in rules.failed}
    print(f"{'Checks':>7}  {'Result':<7} Rule")
    for rule, count in rules.checked.items():
        print(f"{count:>7}  {'FAILED' if rule in failed_rules else 'held':<7} {rule}")
    print(f"\n{sum(rules.checked.values())} checks, {len(rules.failed)} failed (seed {SEED}).")
    for rule, detail in rules.failed[:10]:
        print(f"  FAILED: {rule}: {detail}")
    return len(rules.failed)


# --------------------------------------------------------------------------
# race: several travellers book the last seats at the same moment
# --------------------------------------------------------------------------

def explore_simultaneous_bookings(rounds: int = 20, travellers: int = 8) -> int:
    """Start several bookings at once for a bus with 2 seats and compare what
    each traveller was told with what was saved.

    Booking references are compared, not counts: a count hides the case
    where one traveller is told "confirmed" for a booking that is lost
    while another is told "failed" for a booking that is saved.
    """
    offer = "SR-VAL-0002-S"
    seats = load_all(DATA).departures["SR-VAL-0002"].seats
    print(f"{travellers} travellers each book 1 seat on {offer} at the same moment. The bus has {seats} seats.")
    print("A lock on the bookings file should make the copies of the program take turns.\n")
    print(f"{'Round':<6}{'Told':>10}{'Bookings':>10}{'Confirmed,':>12}{'Saved, but':>12}{'Told':>10}{'Told':>11}")
    print(f"{'':<6}{'confirmed':>10}{'saved':>10}{'not saved':>12}{'told failed':>12}{'sold out':>10}{'not saved':>11}")
    totals = {"lost": 0, "ghost": 0, "overbooked": 0, "clean": 0, "other": 0}
    for number in range(1, rounds + 1):
        with tempfile.TemporaryDirectory() as temp:
            command = [sys.executable, str(ROOT / "main.py"), "book", "--offer", offer,
                       "--data", str(DATA), "--state", f"{temp}/state",
                       "--logs", f"{temp}/logs", "--output", f"{temp}/output"]
            running = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                        text=True, cwd=temp) for _ in range(travellers)]
            printed = [process.communicate()[0] for process in running]
            confirmed = {reference for text in printed if "BOOKING CONFIRMED" in text
                         for reference in REFERENCE.findall(text)[:1]}
            refused = sum("Not booked: this bus is sold out" in text for text in printed)
            not_saved = sum("cannot write bookings file" in text for text in printed)
            other = travellers - len(confirmed) - refused - not_saved
            saved_file = Path(temp) / "state" / "bookings.json"
            rows = json.loads(saved_file.read_text(encoding="utf-8")) if saved_file.exists() else []
            saved = {row["reference"] for row in rows if row["status"] == "confirmed"}
            lost = len(confirmed - saved)        # told confirmed, but the booking is not in the file
            ghost = len(saved - confirmed)       # in the file and holding a seat, but the traveller was told it failed
            totals["lost"] += lost
            totals["ghost"] += ghost
            totals["overbooked"] += len(saved) > seats
            totals["other"] += other
            totals["clean"] += not (lost or ghost or other or len(saved) > seats)
            print(f"{number:<6}{len(confirmed):>10}{len(saved):>10}{lost:>12}{ghost:>12}{refused:>10}{not_saved:>11}")
    print(f"\nOver {rounds} rounds:")
    print(f"  rounds in which every traveller was told what was saved: {totals['clean']}")
    print(f"  rounds in which the bus was overbooked: {totals['overbooked']}")
    print(f"  travellers told 'confirmed' for a booking that was not saved: {totals['lost']}")
    print(f"  bookings saved although the traveller was told it failed: {totals['ghost']}")
    print(f"  travellers who got any other answer: {totals['other']}")
    print("'Told not saved' is the error 'cannot write bookings file'. 'Saved, but told failed' counts the")
    print("bookings among them that were saved all the same: they hold a seat the traveller does not know of.")
    return 0


# --------------------------------------------------------------------------
# trials: how stable and how even-handed the ranking is
# --------------------------------------------------------------------------

PERIODS = ["morning", "afternoon", "evening", "night"]
EXAMPLE_WEIGHTS = {"price": 40.0, "duration": 25.0, "time": 15.0, "flex": 20.0}


def explore_trials() -> int:
    """Measure what the evaluation in the report relies on.

    60 requests are used: each of the 15 cities with each of the 4
    departure periods, for the whole week, one passenger and no optional
    requirement. Every request is ranked under each profile.
    """
    data = load_all(DATA)
    profiles = load_profiles(DATA / PROFILES_FILE)
    offers = data.offers()
    seats = {key: departure.seats for key, departure in data.departures.items()}

    print("1. Does a weight say how much a criterion decides? (the specification's example request)")
    request = cli.build_request(data.destinations, "Malaga", "2027-07-12", "2027-07-14", "morning",
                                budget=50, max_transfers=1, changeable=True)
    ranked = rank_offers(filter_offers(offers, request, seats).accepted, request, check_weights(EXAMPLE_WEIGHTS))
    print(f"   {len(ranked)} ranked offers. Spread of each criterion's contribution to the total:")
    for name, weight in EXAMPLE_WEIGHTS.items():
        spread = statistics.pstdev(scored.contributions[name] for scored in ranked)
        print(f"   {name:<9} weight {weight:>3g}%   standard deviation {spread:5.2f} points")
    print(f"   First and second place are {ranked[0].total - ranked[1].total:.2f} points apart.")

    print("\n2. Which company does each profile recommend? (60 requests)")
    companies = sorted(company.name for company in data.companies.values())
    print(f"   {'Profile':<10}" + "".join(f"{name:>16}" for name in companies)
          + f"{'Near ties':>11}{'Exact ties':>12}")
    night_bus_wins = {period: 0 for period in PERIODS}
    for key, profile in profiles.items():
        weights = check_weights(profile.weights)
        winners = dict.fromkeys(companies, 0)
        near_ties = exact_ties = 0
        for city in data.destinations.values():
            for period in PERIODS:
                wanted = cli.build_request(data.destinations, city.name, "2027-07-10", "2027-07-16", period)
                ranking = rank_offers(filter_offers(offers, wanted, seats).accepted, wanted, weights)
                winner = ranking[0].offer.company.name
                winners[winner] += 1
                near_ties += near_tie_gap(ranking) is not None
                exact_ties += round(ranking[0].total - ranking[1].total, 2) == 0
                night_bus_wins[period] += winner == "NocheVia"
        print(f"   {key:<10}" + "".join(f"{winners[name]:>16}" for name in companies)
              + f"{near_ties:>11}{exact_ties:>12}")
    print("   'Near ties' counts the requests where the first two offers are more than 0 and less than")
    print("   1 point apart: the program prints a note for these. 'Exact ties' have the same total,")
    print("   and the tie rule decides the order.")

    print("\n3. How often does the night company NocheVia rank first, by preferred departure?")
    for period in PERIODS:
        print(f"   {period:<10} {night_bus_wins[period]:>3} of {len(data.destinations) * len(profiles)} rankings")
    return 0


# --------------------------------------------------------------------------
# alternatives: design choices of the scoring model, measured against alternatives
# --------------------------------------------------------------------------

def _order(scored_totals: list[tuple[ScoredOffer, float]]) -> list[tuple[str, float]]:
    """Order (offer, total) pairs with the program's own tie rule."""
    ordered = sorted(scored_totals, key=lambda pair: (
        -pair[1], pair[0].offer.price, pair[0].offer.departure.departure, pair[0].offer.offer_id))
    return [(scored.offer.offer_id, total) for scored, total in ordered]


def _rerank(ranked: list[ScoredOffer], weights: dict[str, float], name: str,
            points_for: "Callable[[ScoredOffer], float]") -> list[tuple[str, float]]:
    """Rank again with the points of one criterion replaced."""
    result = []
    for scored in ranked:
        total = sum(weights[key] / 100 * (points_for(scored) if key == name else scored.scores[key].points)
                    for key in weights)
        result.append((scored, round(total, 2)))
    return _order(result)


def _gap(order: list[tuple[str, float]]) -> float:
    return round(order[0][1] - order[1][1], 2) if len(order) > 1 else 99.0


def explore_alternatives() -> int:
    """Measure three choices of the scoring model against an alternative each.

    The program is not changed: the alternative totals are calculated here
    from the points the program gives.
    """
    data = load_all(DATA)
    profiles = {key: check_weights(profile.weights) for key, profile in load_profiles(DATA / PROFILES_FILE).items()}
    offers = data.offers()
    seats = {key: departure.seats for key, departure in data.departures.items()}

    def ranking(city: str, period: str, weights: dict[str, float], budget: float | None = None,
                without: str | None = None) -> list[ScoredOffer]:
        request = cli.build_request(data.destinations, city, "2027-07-10", "2027-07-16", period, budget=budget)
        accepted = [offer for offer in filter_offers(offers, request, seats).accepted if offer.offer_id != without]
        return rank_offers(accepted, request, weights)

    cities = [city.name for city in data.destinations.values()]
    budgets = {}
    for city in cities:
        prices = sorted(offer.price for offer in offers if offer.departure.destination.name == city)
        budgets[city] = float(round(statistics.median(prices)))

    print("1. Price on a relative scale (the program) against a fixed scale")
    print("   60 requests: each of the 15 cities with each of the 4 departure periods, the whole week,")
    print("   with a budget at the median price of the city, under each of the 4 profiles: 240 rankings.")
    print("   Relative: cheapest offer left 100 points, dearest 0. Fixed: 0 points at the budget,")
    print("   100 points at half the budget or less, whatever the other offers cost.")
    same_winner = reorder_relative = reorder_fixed = near_relative = near_fixed = total = 0
    for weights in profiles.values():
        for city in cities:
            for period in PERIODS:
                budget = budgets[city]
                fixed = lambda scored: max(0.0, min(100.0, (budget - scored.offer.price) / (budget / 2) * 100))
                ranked = ranking(city, period, weights, budget)
                if len(ranked) < 3:
                    continue
                total += 1
                relative_order = [(scored.offer.offer_id, scored.total) for scored in ranked]
                fixed_order = _rerank(ranked, weights, "price", fixed)
                same_winner += relative_order[0][0] == fixed_order[0][0]
                near_relative += 0 < _gap(relative_order) < 1
                near_fixed += 0 < _gap(fixed_order) < 1
                # Take away the offer ranked last and see whether the others keep their order.
                last_relative, last_fixed = relative_order[-1][0], fixed_order[-1][0]
                again = ranking(city, period, weights, budget, without=last_relative)
                reorder_relative += [o.offer.offer_id for o in again] != [i for i, _t in relative_order[:-1]]
                again = ranking(city, period, weights, budget, without=last_fixed)
                reorder_fixed += [i for i, _t in _rerank(again, weights, "price", fixed)] != [i for i, _t in fixed_order[:-1]]
    print(f"   Rankings compared: {total}")
    print(f"   {'':<58}{'Relative':>9}{'Fixed':>8}")
    print(f"   {'First two offers more than 0 and less than 1 point apart':<58}{near_relative:>9}{near_fixed:>8}")
    print(f"   {'Order of the others changes when the last offer is removed':<58}{reorder_relative:>9}{reorder_fixed:>8}")
    print(f"   The two scales recommend the same offer in {same_winner} of {total} rankings.")

    print("\n2. How large must a weight be to decide the ranking? (the 70 percent limit)")
    print("   The same 60 requests. Price gets the weight shown; duration, departure and flexibility")
    print("   share the rest equally. Counted: rankings in which the cheapest offer wins.")
    for weight in (40, 50, 60, 70, 80, 90):
        weights = check_weights({"price": float(weight), "duration": (100 - weight) / 3,
                                 "time": (100 - weight) / 3, "flex": (100 - weight) / 3})
        cheapest_wins = count = 0
        for city in cities:
            for period in PERIODS:
                ranked = ranking(city, period, weights, budgets[city])
                count += 1
                cheapest_wins += ranked[0].offer.price == min(scored.offer.price for scored in ranked)
        print(f"   price weight {weight:>2}%: the cheapest offer wins in {cheapest_wins:>2} of {count}")

    print("\n3. Where to draw the line for a near tie? (the 1 point limit)")
    print("   The 60 requests without a budget, 4 profiles: 240 rankings, as in the trials part.")
    gaps = [_gap([(scored.offer.offer_id, scored.total) for scored in ranking(city, period, weights)])
            for weights in profiles.values() for city in cities for period in PERIODS]
    for limit in (0.5, 1.0, 2.0, 5.0):
        print(f"   first two offers more than 0 and less than {limit:g} points apart: "
              f"{sum(0 < gap < limit for gap in gaps):>3} of {len(gaps)}")

    print("\n4. A missing emissions figure: the median (the program) against the worst score")
    print("   The 60 requests without a budget, environment profile (emissions 40 percent).")
    eco = profiles["eco"]
    best_median, best_worst, sizes, overtaken = [], [], [], 0
    for city in cities:
        for period in PERIODS:
            ranked = ranking(city, period, eco)
            missing = {scored.offer.offer_id for scored in ranked if scored.scores["emissions"].estimated}
            if not missing:
                continue
            worst = _rerank(ranked, eco, "emissions",
                            lambda scored: 0.0 if scored.scores["emissions"].estimated else scored.scores["emissions"].points)
            median_order = [scored.offer.offer_id for scored in ranked]
            worst_order = [offer_id for offer_id, _total in worst]
            best_median.append(1 + min(median_order.index(offer_id) for offer_id in missing))
            best_worst.append(1 + min(worst_order.index(offer_id) for offer_id in missing))
            sizes.append(len(ranked))
            # Offers that report a figure and still rank below the best offer that reports none.
            overtaken += sum(1 for offer_id in median_order[best_median[-1]:] if offer_id not in missing)
    print(f"   Rankings that hold an offer without a figure: {len(sizes)} of {len(cities) * len(PERIODS)}, "
          f"with {statistics.mean(sizes):.0f} offers on average")
    print(f"   Best rank of an offer without a figure, on average: median {statistics.mean(best_median):.1f}, "
          f"worst score {statistics.mean(best_worst):.1f}")
    print(f"   With the median, {overtaken / len(sizes):.0f} offers that report a figure rank below it on average.")
    return 0


def main() -> int:
    """Run the chosen part and return 1 when a crash or a broken rule was found."""
    parser = argparse.ArgumentParser(description="Exploratory tests for BusBidder Spain.")
    parser.add_argument("part", choices=("edge", "rules", "race", "trials", "alternatives"),
                        help="edge: unusual input; rules: rules that must always hold; "
                             "race: bookings at the same moment; trials: stability and "
                             "even-handedness of the ranking; alternatives: design choices "
                             "measured against alternatives")
    part = parser.parse_args().part
    found = {"edge": explore_edge_cases, "rules": explore_rules,
             "race": explore_simultaneous_bookings, "trials": explore_trials,
             "alternatives": explore_alternatives}[part]()
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
