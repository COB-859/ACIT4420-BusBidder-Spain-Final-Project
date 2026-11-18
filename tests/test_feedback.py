"""Tests for the feedback the traveller gets after each action.

Every action should say whether it worked, what changed and what to do
next, in words a traveller uses.
"""

import contextlib
import io
import unittest
from dataclasses import replace
from datetime import date

from busbidder.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main, plain
from busbidder.constraints import CONSTRAINTS, filter_offers
from busbidder.display import format_filter, format_near_tie, format_ranking, format_seats_after
from busbidder.loader import LoadResult
from busbidder.models import count_of
from busbidder.ranking import NEAR_TIE_POINTS, check_weights, near_tie_gap, parse_weights, rank_offers
from tests.helpers import make_departure, make_request
from tests.test_cli import MALAGA, CliTestCase
from tests.test_display import REQUEST, text_of
from tests.test_ranking import SPEC_WEIGHTS, three_offers

DATA_DAYS = (date(2027, 7, 12), date(2027, 7, 13))


class WordingTests(unittest.TestCase):
    def test_count_of_uses_singular_and_plural(self):
        self.assertEqual(count_of(1, "seat"), "1 seat")
        self.assertEqual(count_of(0, "seat"), "0 seats")
        self.assertEqual(count_of(2, "flex seat"), "2 flex seats")

    def test_plain_removes_accents_case_and_outer_spaces(self):
        self.assertEqual(plain("  Málaga "), "malaga")
        self.assertEqual(plain("CÓRDOBA"), "cordoba")
        self.assertEqual(plain("San Sebastián"), "san sebastian")

    def test_a_weight_may_be_written_with_a_percent_sign(self):
        self.assertEqual(parse_weights("price=40%,duration=25%,time=15 %,flex=20%"), SPEC_WEIGHTS)

    def test_seats_after_a_booking(self):
        self.assertEqual(format_seats_after(2), "Seats left on this bus: 2")
        self.assertEqual(format_seats_after(0), "This bus is now sold out.")


class AdviceTests(CliTestCase):
    """When no offer is left, the advice says what is wrong and which value would work.

    The small data folder has five Malaga offers: 25.00 and 30.00 EUR on a bus
    with 3 seats and one transfer, 48.00 and 60.00 EUR on a direct bus with 20
    seats, and 45.00 EUR on a sold-out bus. Only the 60.00 EUR fare is refundable.
    """

    def advice(self, *options: str) -> list[str]:
        """Search with the options and return the lines after 'No buses match'."""
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", *options)
        self.assertEqual(code, EXIT_OK)
        lines = text.strip().splitlines()
        return lines[lines.index("No buses match all your requirements.") + 1:]

    def test_every_constraint_has_its_own_advice(self):
        for name, rule in CONSTRAINTS.items():
            with self.subTest(constraint=name):
                self.assertIsNotNone(rule.problem)
                self.assertIsNotNone(rule.suggest)

    def test_advice_names_the_value_that_would_work(self):
        cases = (
            (["--budget", "5"],
             "5 of the 5 offers cost more than your budget of 5.00 EUR.",
             "The cheapest offer costs 25.00 EUR."),
            (["--passengers", "30"],
             "5 of the 5 offers have fewer than 30 seats left.",
             "The most seats left on one bus is 20."),
            (["--max-duration", "60"],
             "5 of the 5 offers take longer than 1h 00m.",
             "The shortest journey takes 5h 30m."),
            (["--min-luggage", "40"],
             "5 of the 5 offers allow less than 40 kg of luggage.",
             "The highest luggage allowance is 30 kg."),
            (["--refundable", "--budget", "50"],
             "4 of the 5 offers cannot be refunded.",
             "Without this requirement, 3 offers would match."),
        )
        for options, problem, suggestion in cases:
            with self.subTest(options=options):
                self.assertEqual(self.advice(*options), [problem, suggestion])

    def test_the_suggestion_respects_the_other_requirements(self):
        # The cheapest offer costs 25.00 EUR but cannot be changed, so a
        # traveller who needs a changeable ticket must be told 30.00 EUR.
        self.assertEqual(self.advice("--budget", "5", "--changeable"), [
            "5 of the 5 offers cost more than your budget of 5.00 EUR.",
            "The cheapest offer that meets your other requirements costs 30.00 EUR.",
        ])
        # The cheapest direct bus costs 48.00 EUR.
        self.assertEqual(self.advice("--budget", "40", "--max-transfers", "0")[1],
                         "The cheapest offer that meets your other requirements costs 48.00 EUR.")

    def test_advice_says_when_one_change_is_not_enough(self):
        self.assertEqual(self.advice("--passengers", "30", "--budget", "5"), [
            "5 of the 5 offers have fewer than 30 seats left.",
            "Changing this alone is not enough: no offer meets your other requirements either. "
            "Try removing a requirement.",
        ])

    def test_a_journey_limit_of_zero_minutes_is_refused(self):
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", "--max-duration", "0")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Error: longest journey must be at least 1 minute: 0", text)


class NearTieTests(unittest.TestCase):
    """The traveller is told when the first two offers are almost equal."""

    def ranked_with(self, weights: dict) -> list:
        return rank_offers(three_offers(), REQUEST, check_weights(weights))

    def test_gap_is_reported_only_between_0_and_the_limit(self):
        ranked = self.ranked_with(SPEC_WEIGHTS)                # totals 70.0, 67.5, 27.5
        self.assertIsNone(near_tie_gap(ranked))
        self.assertEqual(format_near_tie(ranked), [])
        self.assertIsNone(near_tie_gap(ranked[:1]))
        self.assertIsNone(near_tie_gap([]))

    def test_a_gap_below_one_point_gets_a_note(self):
        first, second, third = self.ranked_with(SPEC_WEIGHTS)
        close = [first, replace(second, total=first.total - 0.42), third]
        self.assertEqual(near_tie_gap(close), 0.42)
        self.assertEqual(format_near_tie(close), [
            "Note: the first two offers are only 0.42 points apart, so a small change in the "
            "weights can change which one ranks first.",
        ])
        text = text_of(format_ranking(close, check_weights(SPEC_WEIGHTS), {}, top=0))
        self.assertIn("only 0.42 points apart", text)

    def test_the_limit_itself_and_an_exact_tie_get_no_note(self):
        first, second, third = self.ranked_with(SPEC_WEIGHTS)
        self.assertIsNone(near_tie_gap([first, replace(second, total=first.total - NEAR_TIE_POINTS), third]))
        self.assertIsNone(near_tie_gap([first, replace(second, total=first.total), third]))


class CoverageTests(unittest.TestCase):
    """The traveller is told which days have buses."""

    def test_travel_days_are_the_first_and_last_day_in_the_data(self):
        self.assertIsNone(LoadResult().travel_days())
        departures = {
            "SR-MAL-0001": make_departure(serial=1),
            "SR-MAL-0002": make_departure(serial=2),
        }
        first, last = LoadResult(departures=departures).travel_days()
        self.assertLessEqual(first, last)
        self.assertEqual(first, min(d.departure.date() for d in departures.values()))

    def test_no_bus_on_the_dates_names_the_days_with_buses(self):
        text = text_of(format_filter(filter_offers([], REQUEST, {}), REQUEST, DATA_DAYS))
        self.assertIn("No buses found for these dates, so there is nothing to rank.", text)
        self.assertIn("BusBidder has buses from 12 July 2027 to 13 July 2027.", text)

    def test_dates_partly_outside_the_data_get_a_note(self):
        offers = three_offers()
        outside = make_request(date_from=date(2027, 7, 12), date_to=date(2027, 7, 20))
        text = text_of(format_filter(filter_offers(offers, outside, {}), outside, DATA_DAYS))
        self.assertIn("Note: some of your dates have no buses. BusBidder has buses from", text)

    def test_dates_inside_the_data_get_no_note(self):
        inside = make_request(date_from=date(2027, 7, 12), date_to=date(2027, 7, 13))
        text = text_of(format_filter(filter_offers(three_offers(), inside, {}), inside, DATA_DAYS))
        self.assertNotIn("Note:", text)


class LowSeatTests(unittest.TestCase):
    """A bus that could not take the party twice gets a note."""

    def ranking_text(self, seats: int, passengers: int) -> str:
        weights = check_weights(SPEC_WEIGHTS)
        ranked = rank_offers(three_offers(), REQUEST, weights)
        every_bus = {scored.offer.departure.departure_id: seats for scored in ranked}
        return text_of(format_ranking(ranked, weights, every_bus, top=1, passengers=passengers))

    def test_one_passenger_is_warned_at_the_last_seat_only(self):
        self.assertIn("Note: only 1 seat left on this bus.", self.ranking_text(1, 1))
        self.assertNotIn("Note: only", self.ranking_text(2, 1))

    def test_a_party_is_warned_below_twice_its_size(self):
        self.assertIn("Note: only 7 seats left on this bus.", self.ranking_text(7, 4))
        self.assertNotIn("Note: only", self.ranking_text(8, 4))


class CommandFeedbackTests(CliTestCase):
    def run_usage_error(self, *arguments: str) -> tuple[int, str]:
        """Run a command the parser refuses; return its exit code and message."""
        message = io.StringIO()
        with contextlib.redirect_stderr(message), contextlib.redirect_stdout(io.StringIO()), \
                self.assertRaises(SystemExit) as caught:
            main(list(arguments))
        return caught.exception.code, message.getvalue()

    def test_a_mistake_on_the_command_line_is_reported_in_one_line(self):
        cases = (
            (["search", *MALAGA, "--profile", "budget", "--passengers", "two"],
             "Error: argument --passengers: must be a whole number: 'two'.", "main.py search"),
            (["search", *MALAGA, "--profile", "budget", "--top", "-3"],
             "Error: argument --top: must be 0 or more: '-3'.", "main.py search"),
            (["search", *MALAGA, "--profile", "budget", "--weights", "price=100"],
             "Error: argument --weights: not allowed with argument --profile.", "main.py search"),
            (["search", *MALAGA], "Error: one of the arguments --profile --weights is required.",
             "main.py search"),
            (["frobnicate"], "Error: argument command: invalid choice: 'frobnicate'", "main.py"),
        )
        for arguments, reason, program in cases:
            with self.subTest(arguments=arguments[-2:]):
                code, text = self.run_usage_error(*arguments)
                self.assertEqual(code, EXIT_USAGE)
                self.assertIn(reason, text)
                self.assertIn(f"Run 'python3 {program} --help' to see the options.", text)
                self.assertNotIn("usage:", text)
                self.assertEqual(len(text.strip().splitlines()), 2)

    def test_zero_passengers_keeps_its_own_message(self):
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", "--passengers", "0")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("passengers must be at least 1", text)

    def test_destination_with_an_accent_is_found(self):
        arguments = ["--destination", "Málaga", *MALAGA[2:], "--profile", "budget"]
        code, text = self.run_main("search", *arguments)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Journey: Madrid to Malaga", text)

    def test_weights_with_percent_signs_rank_like_plain_numbers(self):
        _code, plain_text = self.run_main(
            "search", *MALAGA, "--weights", "price=40,duration=25,time=15,flex=20")
        code, percent_text = self.run_main(
            "search", *MALAGA, "--weights", "price=40%,duration=25%,time=15%,flex=20%")
        self.assertEqual(code, EXIT_OK)
        self.assertEqual(percent_text, plain_text)

    def test_search_ends_with_how_to_book_the_top_offer(self):
        code, text = self.run_main("search", *MALAGA, "--profile", "budget", "--passengers", "2")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Next step: to book an offer, use its offer ID. For the top offer:", text)
        self.assertTrue(text.rstrip().endswith(
            "python3 main.py book --offer SR-MAL-0001-B --passengers 2"))

    def test_compare_ends_with_a_next_step(self):
        _code, text = self.run_main("compare", *MALAGA)
        self.assertIn("Next step: to see the full ranking for one profile", text)

    def test_dates_outside_the_data_name_the_days_with_buses(self):
        arguments = ["--destination", "Malaga", "--date-from", "2027-08-01", "--date-to",
                     "2027-08-02", "--depart", "morning", "--profile", "budget"]
        code, text = self.run_main("search", *arguments)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("BusBidder has buses from 12 July 2027 to 13 July 2027.", text)
        self.assertNotIn("Next step", text)

    def test_booking_the_last_seats_says_the_bus_is_sold_out(self):
        code, text = self.run_main("book", "--offer", "SR-MAL-0001-B", "--passengers", "3")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Booking confirmed: 3 basic seats to Malaga for 75.00 EUR.", text)
        self.assertIn("This bus is now sold out.", text)

    def test_booking_one_seat_uses_the_singular(self):
        _code, text = self.run_main("book", "--offer", "CE-MAL-0001-F")
        self.assertIn("Booking confirmed: 1 flex seat to Malaga for 60.00 EUR.", text)

    def test_booking_list_ends_with_a_summary_and_how_to_cancel(self):
        self.run_main("book", "--offer", "CE-MAL-0001-F")
        _code, booked = self.run_main("book", "--offer", "SR-MAL-0001-B")
        reference = next(line.split()[-1] for line in booked.splitlines()
                         if line.startswith("Reference:"))
        _code, text = self.run_main("bookings")
        self.assertIn("2 bookings: 2 confirmed, 0 cancelled.", text)
        self.assertIn("To cancel a confirmed booking, use the cancel command with its reference.", text)
        self.run_main("cancel", "--ref", reference)
        _code, text = self.run_main("bookings")
        self.assertIn("2 bookings: 1 confirmed, 1 cancelled.", text)

    def test_only_cancelled_bookings_do_not_offer_to_cancel(self):
        _code, booked = self.run_main("book", "--offer", "SR-MAL-0001-B")
        reference = next(line.split()[-1] for line in booked.splitlines()
                         if line.startswith("Reference:"))
        self.run_main("cancel", "--ref", reference)
        _code, text = self.run_main("bookings")
        self.assertIn("1 booking: 0 confirmed, 1 cancelled.", text)
        self.assertNotIn("To cancel", text)

    def test_validate_ends_with_a_result_when_only_a_profile_is_invalid(self):
        (self.data_dir / "profiles.json").write_text(
            '{"broken": {"description": "Wrong total", '
            '"weights": {"price": 50, "duration": 20, "time": 10, "flex": 10}}}',
            encoding="utf-8",
        )
        code, text = self.run_main("validate")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Result: 1 profile in profiles.json is invalid.", text)

    def test_a_result_that_cannot_be_saved_says_it_was_still_shown(self):
        blocked = self.folder / "a_file"
        blocked.write_text("not a folder", encoding="utf-8")
        code, text = self.run_main(
            "search", *MALAGA, "--profile", "budget", "--save", str(blocked / "result.txt"))
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("#1  SR-MAL-0001-B", text)
        self.assertIn("Error: the result is shown above, but it could not be saved to", text)


if __name__ == "__main__":
    unittest.main()
