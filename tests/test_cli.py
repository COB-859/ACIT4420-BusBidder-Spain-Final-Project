"""Tests for the command-line interface."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from busbidder.cli import EXIT_ERROR, EXIT_OK, main
from busbidder.validation import BOOKING_REFERENCE_PATTERN
from tests.helpers import SMALL_DATA, write_data_dir


class CliTestCase(unittest.TestCase):
    """Runs the program against a small data folder in a temporary place."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.data_dir = write_data_dir(self.folder / "data")
        self.log_dir = self.folder / "logs"
        self.output_dir = self.folder / "output"
        self.state_dir = self.folder / "state"

    def run_main(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        folders = ["--data", str(self.data_dir), "--logs", str(self.log_dir),
                   "--output", str(self.output_dir), "--state", str(self.state_dir)]
        with contextlib.redirect_stdout(output):
            code = main([*folders, *arguments])
        return code, output.getvalue()

    def read_log(self) -> str:
        return (self.log_dir / "busbidder.log").read_text(encoding="utf-8")


class StartTests(CliTestCase):
    def test_help_lists_the_commands(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        for command in ("validate", "search", "compare", "book", "bookings"):
            self.assertIn(command, output.getvalue())

    def test_version_option_exits_with_zero(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("BusBidder Spain", output.getvalue())


class ValidateTests(CliTestCase):
    def test_reports_counts_for_valid_data(self):
        code, text = self.run_main("validate")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Departures:   4 accepted (1 sold out", text)
        self.assertIn("Fares:        6 accepted", text)
        self.assertIn("Rejected rows: 0", text)

    def test_lists_rejected_rows_and_writes_the_file(self):
        fares = SMALL_DATA["fares.csv"].replace("25.00", "-25.00")
        write_data_dir(self.data_dir, fares_csv=fares)
        code, text = self.run_main("validate")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("fares.csv, line 2: price must be above 0", text)
        saved = (self.output_dir / "rejected_records.txt").read_text(encoding="utf-8")
        self.assertIn("fares.csv, line 2", saved)

    def test_rejection_and_sold_out_are_written_to_the_log(self):
        fares = SMALL_DATA["fares.csv"].replace("25.00", "-25.00")
        write_data_dir(self.data_dir, fares_csv=fares)
        self.run_main("validate")
        log = self.read_log()
        self.assertIn("WARNING busbidder.loader: Rejected fares.csv, line 2", log)
        self.assertIn("1 departures are sold out", log)
        self.assertIn("INFO busbidder: Command: validate", log)

    def test_missing_data_file_gives_an_error_message_and_code(self):
        (self.data_dir / "departures.csv").unlink()
        code, text = self.run_main("validate")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Error: data file not found", text)
        self.assertIn("ERROR busbidder: data file not found", self.read_log())

    def test_folder_options_also_work_after_the_command(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["validate", "--data", str(self.data_dir), "--logs", str(self.log_dir),
                         "--output", str(self.output_dir)])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Fares:        6 accepted", output.getvalue())


MALAGA = ["--destination", "Malaga", "--date-from", "2027-07-12", "--date-to", "2027-07-14",
          "--depart", "morning"]


class SearchTests(CliTestCase):
    def test_search_with_a_profile_ranks_and_explains(self):
        code, text = self.run_main("search", *MALAGA, "--profile", "budget")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Mandatory conditions", text)
        self.assertIn("Preferences (scored", text)
        self.assertIn("weights from: profile 'budget' (Price first)", text)
        self.assertIn("Offers for this city and these dates: 5", text)
        self.assertIn("removed per condition (an offer can fail several): seats 1", text)
        self.assertIn("#1  SR-MAL-0001-B", text)
        self.assertIn("Why #1 ranks first:", text)

    def test_search_with_typed_weights(self):
        code, text = self.run_main("search", *MALAGA, "--weights", "price=10,duration=10,time=10,flex=70")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("weights from: typed by the traveller", text)
        self.assertIn("#1  CE-MAL-0001-F", text)

    def test_mandatory_conditions_remove_offers(self):
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", "--changeable",
                                   "--budget", "50", "--show-removed")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Offers left to rank: 2", text)
        self.assertIn("SR-MAL-0001-B: ticket cannot be changed", text)
        self.assertIn("CE-MAL-0001-F: price 60.00 EUR is above the budget", text)
        self.assertIn("#1  SR-MAL-0001-S", text)

    def test_destination_can_be_given_as_a_code_in_any_case(self):
        arguments = ["--destination", "tol", *MALAGA[2:], "--profile", "budget"]
        code, text = self.run_main("search", *arguments)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Journey: Madrid to Toledo", text)
        self.assertIn("only offer that meets the mandatory conditions", text)

    def test_no_offer_left_is_reported_and_is_not_an_error(self):
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", "--budget", "5")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("No offer meets every mandatory condition", text)
        self.assertNotIn("#1", text)

    def test_dates_without_offers(self):
        arguments = ["--destination", "Malaga", "--date-from", "2027-08-01", "--date-to",
                     "2027-08-02", "--depart", "morning", "--profile", "budget"]
        code, text = self.run_main("search", *arguments)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Offers for this city and these dates: 0", text)

    def test_bad_requests_give_an_error_message_and_code(self):
        cases = (
            (["--destination", "Atlantis", *MALAGA[2:], "--profile", "budget"], "unknown destination"),
            ([*MALAGA[:3], "12 July", *MALAGA[4:], "--profile", "budget"], "first travel date must look like"),
            ([*MALAGA[:5], "2027-07-01", *MALAGA[6:], "--profile", "budget"], "is before first travel date"),
            ([*MALAGA[:7], "brunch", "--profile", "budget"], "preferred departure must be one of"),
            ([*MALAGA, "--profile", "budget", "--passengers", "0"], "passengers must be at least 1"),
            ([*MALAGA, "--profile", "budget", "--budget", "-5"], "budget must be above 0"),
            ([*MALAGA, "--profile", "luxury"], "unknown profile"),
            ([*MALAGA, "--weights", "price=100"], "must use price, duration, time and flex"),
            ([*MALAGA, "--weights", "price=40,duration=25,time=15,flex=10"], "must total 100"),
        )
        for arguments, message in cases:
            with self.subTest(message=message):
                code, text = self.run_main("search", *arguments)
                self.assertEqual(code, EXIT_ERROR)
                self.assertIn(message, text)

    def test_save_writes_the_same_text_to_a_file(self):
        target = self.output_dir / "malaga.txt"
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", "--save", str(target))
        self.assertEqual(code, EXIT_OK)
        saved = target.read_text(encoding="utf-8")
        self.assertIn("#1  SR-MAL-0001-B", saved)
        self.assertIn(f"Saved to {target}", text)

    def test_search_is_recorded_in_the_log(self):
        self.run_main("search", *MALAGA, "--profile", "budget")
        self.assertIn("Search: Malaga, 2027-07-12 to 2027-07-14", self.read_log())
        self.assertIn("top offer SR-MAL-0001-B", self.read_log())

    def test_same_search_twice_gives_the_same_output(self):
        first = self.run_main("search", *MALAGA, "--profile", "budget")
        second = self.run_main("search", *MALAGA, "--profile", "budget")
        self.assertEqual(first, second)


class CompareTests(CliTestCase):
    def test_all_profiles_are_compared_on_the_same_offers(self):
        code, text = self.run_main("compare", *MALAGA)
        self.assertEqual(code, EXIT_OK)
        self.assertRegex(text, r"budget\s+SR-MAL-0001-B")
        self.assertRegex(text, r"flexible\s+CE-MAL-0001-F")
        self.assertIn("The 2 profiles recommend 2 different offer(s).", text)
        self.assertIn("Profile 'flexible': Flexibility first", text)

    def test_chosen_profiles_only(self):
        code, text = self.run_main("compare", *MALAGA, "--profiles", "budget")
        self.assertEqual(code, EXIT_OK)
        self.assertNotIn("Profile 'flexible'", text)

    def test_unknown_profile_is_an_error(self):
        code, text = self.run_main("compare", *MALAGA, "--profiles", "budget", "luxury")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("unknown profile: 'luxury'", text)

    def test_nothing_to_compare_is_reported(self):
        code, text = self.run_main("compare", *MALAGA, "--budget", "5")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("No offer meets every mandatory condition", text)
        self.assertNotIn("Profile comparison", text)


class BookTests(CliTestCase):
    def test_book_prints_a_confirmation_and_saves_the_booking(self):
        code, text = self.run_main("book", "--offer", "SR-MAL-0001-S", "--passengers", "2")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("BOOKING CONFIRMED", text)
        self.assertRegex(text, r"Reference:      BB-\d{8}-[A-HJ-NP-Z2-9]{6}")
        self.assertIn("Total price:    60.00 EUR", text)
        self.assertIn("Seats left on departure SR-MAL-0001: 1", text)
        self.assertTrue((self.state_dir / "bookings.json").is_file())

    def test_a_booking_reduces_the_seats_shown_by_search_for_every_fare(self):
        _code, before = self.run_main("search", *MALAGA, "--profile", "budget")
        self.assertIn("3 seat(s) left", before)
        self.run_main("book", "--offer", "SR-MAL-0001-S", "--passengers", "2")
        _code, after = self.run_main("search", *MALAGA, "--profile", "budget")
        self.assertNotIn("3 seat(s) left", after)
        self.assertEqual(after.count("direct, 1 seat(s) left") + after.count("1 transfer(s), 1 seat(s) left"), 2)

    def test_departure_filled_by_bookings_disappears_from_search(self):
        self.run_main("book", "--offer", "SR-MAL-0001-B", "--passengers", "3")
        code, text = self.run_main("search", *MALAGA, "--profile", "budget")
        self.assertEqual(code, EXIT_OK)
        self.assertNotIn("#1  SR-MAL-0001", text)
        self.assertIn("seats 3", text)

    def test_booking_more_than_the_seats_left_is_an_error(self):
        code, text = self.run_main("book", "--offer", "SR-MAL-0001-B", "--passengers", "4")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("has 3 seat(s) left, 4 requested", text)
        self.assertFalse((self.state_dir / "bookings.json").exists())

    def test_unknown_offer_is_an_error(self):
        code, text = self.run_main("book", "--offer", "XX-XXX-0001-B")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("unknown offer", text)

    def test_bookings_lists_what_was_booked(self):
        code, text = self.run_main("bookings")
        self.assertEqual((code, text.strip()), (EXIT_OK, "No bookings saved."))
        self.run_main("book", "--offer", "CE-MAL-0001-F")
        code, text = self.run_main("bookings")
        self.assertIn("CE-MAL-0001-F", text)
        self.assertIn("confirmed", text)

    def test_booking_is_recorded_in_the_log(self):
        self.run_main("book", "--offer", "SR-MAL-0001-S")
        self.assertIn("INFO busbidder.booking: Booked BB-", self.read_log())

    def test_broken_bookings_file_is_reported(self):
        self.state_dir.mkdir()
        (self.state_dir / "bookings.json").write_text("{broken", encoding="utf-8")
        code, text = self.run_main("bookings")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("cannot read bookings file", text)


class GuidedTests(CliTestCase):
    ANSWERS = ["Malaga", "2027-07-12", "2027-07-14", "", "morning",
               "50", "", "", "y", "", "", "budget"]

    def run_guided(self, answers):
        """Run the guided mode with the given answers typed in order."""
        with mock.patch("builtins.input", side_effect=answers):
            return self.run_main()

    def test_guided_search_then_booking(self):
        code, text = self.run_guided([*self.ANSWERS, "1"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("  - price per person at most 50.00 EUR", text)
        self.assertIn("  - ticket must be changeable", text)
        self.assertIn("#1  SR-MAL-0001-S", text)
        self.assertIn("BOOKING CONFIRMED", text)
        self.assertIn("Offer:          SR-MAL-0001-S", text)
        self.assertRegex(text, BOOKING_REFERENCE_PATTERN.pattern.strip("^$"))

    def test_enter_at_the_end_books_nothing(self):
        code, text = self.run_guided([*self.ANSWERS, ""])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Nothing booked.", text)
        self.assertFalse((self.state_dir / "bookings.json").exists())

    def test_bad_answers_are_asked_again(self):
        answers = ["Atlantis", "Malaga", "12 July", "2027-07-12", "2027-07-01", "2027-07-14",
                   "zero", "0", "2", "brunch", "morning", "-5", "50", "", "", "maybe", "y", "", "",
                   "luxury", "budget", "99", ""]
        code, text = self.run_guided(answers)
        self.assertEqual(code, EXIT_OK)
        for message in (
            "unknown destination: 'Atlantis'",
            "first travel date must look like 2027-07-12",
            "last travel date is before the first travel date",
            "passengers must be a number",
            "passengers must be at least 1",
            "preferred departure must be one of",
            "budget must be at least 0.01",
            "answer y or n",
            "unknown profile: 'luxury'",
            "rank number must be at most",
        ):
            with self.subTest(message=message):
                self.assertIn(message, text)
        self.assertIn("2 passenger(s)", text)
        self.assertIn("Nothing booked.", text)

    def test_custom_weights_can_be_typed(self):
        answers = [*self.ANSWERS[:-1], "custom", "price=100", "price=10,duration=10,time=10,flex=70", ""]
        code, text = self.run_guided(answers)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("must use price, duration, time and flex", text)
        self.assertIn("weights from: typed by the traveller", text)

    def test_no_offer_left_ends_without_asking_to_book(self):
        answers = ["Malaga", "2027-07-12", "2027-07-14", "", "morning", "5", "", "", "", "", "", "budget"]
        code, text = self.run_guided(answers)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("No offer meets every mandatory condition", text)
        self.assertNotIn("Rank number", text)

    def test_end_of_input_stops_quietly(self):
        with mock.patch("builtins.input", side_effect=EOFError):
            code, text = self.run_main()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Stopped.", text)


if __name__ == "__main__":
    unittest.main()
