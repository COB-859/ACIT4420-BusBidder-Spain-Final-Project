"""Tests at exact limits, added after fault injection.

tools/fault_injection.py changes one operator at a time and runs the
tests. The changes below were not noticed by any test, so each test here
sits exactly on the limit that the change would move.
"""

import contextlib
import io
import tempfile
import unittest
from dataclasses import replace
from datetime import time
from pathlib import Path
from unittest import mock

from busbidder.booking import BookingStore, SeatLedger
from busbidder.cli import (DEFAULT_DATA_DIR, DEFAULT_LOG_DIR, DEFAULT_OUTPUT_DIR, DEFAULT_STATE_DIR, EXIT_OK,
                           build_parser, main)
from busbidder.display import format_confirmation
from busbidder.criteria import transfers_score
from busbidder.explain import DISADVANTAGE_BELOW_POINTS, explain_top
from busbidder.loader import write_rejected
from busbidder.models import TimeBand
from busbidder.ranking import check_weights, near_tie_gap, rank_offers
from tests.helpers import make_departure, make_fare, make_offer, make_request
from tests.test_booking import make_booking
from tests.test_cli import MALAGA, CliTestCase
from tests.test_ranking import SPEC_WEIGHTS, three_offers

REQUEST = make_request()


class TimeBandEdgeTests(unittest.TestCase):
    def test_both_edges_of_a_band_are_inside(self):
        morning = TimeBand.from_text("morning")                 # 06:00 to 12:00
        self.assertEqual(morning.minutes_outside(time(6, 0)), 0)
        self.assertEqual(morning.minutes_outside(time(12, 0)), 0)
        self.assertEqual(morning.minutes_outside(time(5, 59)), 1)
        self.assertEqual(morning.minutes_outside(time(12, 1)), 1)

    def test_both_edges_of_a_band_over_midnight_are_inside(self):
        night = TimeBand.from_text("night")                     # 22:00 to 06:00
        self.assertEqual(night.minutes_outside(time(22, 0)), 0)
        self.assertEqual(night.minutes_outside(time(6, 0)), 0)
        self.assertEqual(night.minutes_outside(time(21, 59)), 1)
        self.assertEqual(night.minutes_outside(time(6, 1)), 1)


class SmallestValidValueTests(unittest.TestCase):
    def test_emissions_of_exactly_zero_are_valid(self):
        self.assertEqual(make_departure(emissions_kg=0.0).emissions_kg, 0.0)

    def test_a_journey_limit_of_one_minute_is_valid(self):
        self.assertEqual(make_request(max_duration_minutes=1).max_duration_minutes, 1)

    def test_a_luggage_allowance_of_zero_is_valid(self):
        self.assertEqual(make_fare(luggage_kg=0).luggage_kg, 0)

    def test_two_offers_are_enough_for_a_near_tie(self):
        first, second, _third = rank_offers(three_offers(), REQUEST, check_weights(SPEC_WEIGHTS))
        self.assertEqual(near_tie_gap([first, replace(second, total=first.total - 0.25)]), 0.25)


class TransferTextTests(unittest.TestCase):
    def test_no_transfer_is_called_direct(self):
        offers = [make_offer("B", serial=number + 1, transfers=number) for number in range(3)]
        scores = transfers_score(offers, REQUEST)
        self.assertEqual([score.value for score in scores], ["direct", "1 transfer", "2 transfers"])
        self.assertEqual([score.points for score in scores], [100.0, 50.0, 0.0])


class ExplanationLimitTests(unittest.TestCase):
    """A criterion with exactly 60 points counts as a strength, not as the disadvantage."""

    def at_the_limit(self):
        ranked = rank_offers(three_offers(), REQUEST, check_weights(SPEC_WEIGHTS))
        top = ranked[0]
        # Every criterion well above the limit, and price exactly on it.
        scores = {name: replace(score, points=90.0) for name, score in top.scores.items()}
        scores["price"] = replace(top.scores["price"], points=DISADVANTAGE_BELOW_POINTS)
        contributions = dict.fromkeys(top.contributions, 1.0)
        contributions["price"] = 50.0
        return [replace(top, scores=scores, contributions=contributions), *ranked[1:]]

    def test_exactly_sixty_points_is_a_strength(self):
        text = "\n".join(explain_top(self.at_the_limit()))
        self.assertIn("Of the criteria it scores well on, the largest contributions come from price", text)

    def test_exactly_sixty_points_is_not_a_disadvantage(self):
        text = "\n".join(explain_top(self.at_the_limit()))
        self.assertNotIn("Remaining disadvantage", text)

    def test_just_below_sixty_points_is_the_disadvantage(self):
        ranked = self.at_the_limit()
        scores = dict(ranked[0].scores)
        scores["price"] = replace(scores["price"], points=DISADVANTAGE_BELOW_POINTS - 0.1)
        text = "\n".join(explain_top([replace(ranked[0], scores=scores), *ranked[1:]]))
        self.assertIn("Remaining disadvantage: price", text)
        self.assertNotIn("largest contributions come from price", text)


class ExactTieTests(unittest.TestCase):
    def test_an_exact_tie_is_explained_by_the_tie_rule_only(self):
        # Two offers that differ only in their ID have the same total.
        twins = [make_offer("B", serial=1), make_offer("B", serial=2)]
        ranked = rank_offers(twins, REQUEST, check_weights(SPEC_WEIGHTS))
        self.assertEqual(ranked[0].total, ranked[1].total)
        text = "\n".join(explain_top(ranked))
        self.assertIn("It has the same total as", text)
        self.assertNotIn("It is ahead of", text)


class NewFolderTests(unittest.TestCase):
    """A file can be written into a folder whose parent does not exist yet."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.deep = Path(temp.name) / "one" / "two"

    def test_bookings_are_saved_two_new_folders_deep(self):
        store = BookingStore(self.deep / "bookings.json")
        store.save([make_booking()])
        self.assertEqual(len(store.load()), 1)

    def test_rejected_rows_are_written_two_new_folders_deep(self):
        write_rejected([], self.deep / "rejected_records.txt")
        self.assertIn("Rejected rows: 0", (self.deep / "rejected_records.txt").read_text(encoding="utf-8"))


class LockEdgeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)

    def test_the_lock_can_be_taken_two_new_folders_deep(self):
        store = BookingStore(self.folder / "one" / "two" / "bookings.json")
        with store.locked():
            self.assertTrue(store.lock_path.exists())

    def test_a_lock_that_is_already_gone_is_not_an_error(self):
        store = BookingStore(self.folder / "bookings.json")
        with store.locked():
            store.lock_path.unlink()
        self.assertFalse(store.lock_path.exists())


class FullBusTests(unittest.TestCase):
    def test_a_bus_that_is_exactly_full_is_not_reported_as_overbooked(self):
        departures = {"SR-MAL-0001": make_departure(seats=2)}
        with self.assertNoLogs("busbidder.booking", level="WARNING"):
            ledger = SeatLedger(departures, [make_booking(passengers=2)])
        self.assertEqual(ledger.seats_left("SR-MAL-0001"), 0)

    def test_one_seat_too_many_is_reported(self):
        departures = {"SR-MAL-0001": make_departure(seats=2)}
        with self.assertLogs("busbidder.booking", level="WARNING") as logs:
            SeatLedger(departures, [make_booking(passengers=3)])
        self.assertIn("has 2 seats but 3 are booked", logs.output[0])


class ArrivalTextTests(unittest.TestCase):
    def test_an_arrival_on_the_same_day_shows_the_time_only(self):
        self.assertIn("Arrival:        14:00", format_confirmation(make_booking()))

    def test_an_arrival_on_the_next_day_shows_the_day(self):
        overnight = replace(make_booking(), arrival=make_booking().arrival.replace(day=13, hour=0, minute=30))
        self.assertIn("Arrival:        Tue 13 Jul 00:30", format_confirmation(overnight))


class DefaultFolderTests(unittest.TestCase):
    def test_the_folders_have_defaults_when_none_is_given(self):
        args = build_parser().parse_args(["validate"])
        self.assertEqual((args.data, args.state, args.logs, args.output),
                         (DEFAULT_DATA_DIR, DEFAULT_STATE_DIR, DEFAULT_LOG_DIR, DEFAULT_OUTPUT_DIR))


class SearchOptionEdgeTests(CliTestCase):
    def test_top_zero_is_accepted_and_shows_every_offer(self):
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", "--top", "0")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("#1  ", text)
        self.assertIn("#4  ", text)
        self.assertNotIn("Showing", text)

    def test_a_result_is_saved_two_new_folders_deep(self):
        target = self.folder / "one" / "two" / "result.txt"
        code, _text = self.run_main("search", *MALAGA, "--profile", "budget", "--save", str(target))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("#1  ", target.read_text(encoding="utf-8"))

    def test_the_guided_mode_does_not_list_the_removed_offers(self):
        answers = ["Malaga", "2027-07-12", "2027-07-14", "", "morning", "40", "", "", "", "", "", "budget", ""]
        with mock.patch("builtins.input", side_effect=answers):
            code, text = self.run_main()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Removed", text)
        self.assertNotIn("Removed offers and the reasons:", text)


class GuidedSingleDayTests(CliTestCase):
    def test_the_last_travel_day_may_be_the_first(self):
        answers = ["Malaga", "2027-07-12", "2027-07-12", "", "morning", "", "", "", "", "", "", "budget", ""]
        with mock.patch("builtins.input", side_effect=answers):
            code, text = self.run_main()
        self.assertEqual(code, EXIT_OK)
        self.assertNotIn("before the first travel date", text)
        self.assertIn("Journey: Madrid to Malaga, 2027-07-12 to 2027-07-12", text)


class RequiredOptionTests(CliTestCase):
    def run_usage_error(self, *arguments: str) -> tuple[int, str]:
        """Run a command the parser refuses; return its exit code and message."""
        message = io.StringIO()
        with contextlib.redirect_stderr(message), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as caught:
            main(list(arguments))
        return caught.exception.code, message.getvalue()

    def test_destination_and_departure_must_be_given(self):
        complete = dict(zip(MALAGA[::2], MALAGA[1::2]))
        for missing in ("--destination", "--depart", "--date-from", "--date-to"):
            with self.subTest(missing=missing):
                arguments = [part for option, value in complete.items() if option != missing
                             for part in (option, value)]
                code, text = self.run_usage_error("search", *arguments, "--profile", "budget")
                self.assertEqual(code, 2)
                self.assertIn(f"the following arguments are required: {missing}", text)


    def test_an_option_must_be_typed_in_full(self):
        # "--to" is the start of "--top" and must not be taken for it.
        code, text = self.run_usage_error("search", *MALAGA, "--profile", "budget", "--to", "3")
        self.assertEqual(code, 2)
        self.assertIn("unrecognized arguments: --to 3", text)
        self.assertNotIn("--top", text)

    def test_book_needs_an_offer_and_cancel_needs_a_reference(self):
        for command, missing in (("book", "--offer"), ("cancel", "--ref")):
            with self.subTest(command=command):
                code, text = self.run_usage_error(command)
                self.assertEqual(code, 2)
                self.assertIn(f"the following arguments are required: {missing}", text)


if __name__ == "__main__":
    unittest.main()
