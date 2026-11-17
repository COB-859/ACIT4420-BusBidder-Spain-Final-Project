"""Tests for the text shown to the user."""

import unittest

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
    format_when,
)
from busbidder.ranking import check_weights, rank_offers
from tests.helpers import make_departure, make_offer, make_request
from tests.test_booking import make_booking
from tests.test_ranking import SPEC_WEIGHTS, three_offers

SEATS = {"SR-MAL-0001": 12, "SR-MAL-0002": 5, "SR-MAL-0003": 1}
REQUEST = make_request(budget=50, max_transfers=1)


def text_of(lines) -> str:
    return "\n".join(lines)


class RequestTextTests(unittest.TestCase):
    def test_request_lists_the_mandatory_conditions(self):
        text = text_of(format_request(REQUEST))
        self.assertIn("Journey: Madrid to Malaga, 2027-07-12 to 2027-07-14, 1 passenger(s)", text)
        self.assertIn("Mandatory conditions", text)
        self.assertIn("  - price per person at most 50.00 EUR", text)
        self.assertIn("  - at most 1 transfer(s)", text)

    def test_preferences_are_shown_apart_from_the_conditions(self):
        text = text_of(format_preferences(REQUEST, SPEC_WEIGHTS, "profile 'budget'"))
        self.assertIn("Preferences (scored", text)
        self.assertIn("Price 40%, Duration 25%, Departure 15%, Flexibility 20%", text)
        self.assertIn("preferred departure: morning (06:00 to 12:00)", text)
        self.assertIn("weights from: profile 'budget'", text)
        self.assertNotIn("Note:", text)

    def test_a_dominant_weight_gets_a_note(self):
        weights = {"price": 85, "duration": 5, "time": 5, "flex": 5}
        text = text_of(format_preferences(REQUEST, weights, "typed"))
        self.assertIn("Note: Price has a weight above 70%", text)


class FilterTextTests(unittest.TestCase):
    def test_counts_per_condition(self):
        offers = three_offers() + [make_offer("B", serial=4, price=60, transfers=2)]
        result = filter_offers(offers, REQUEST, {**SEATS, "SR-MAL-0004": 9})
        text = text_of(format_filter(result))
        self.assertIn("Offers for this city and these dates: 4", text)
        self.assertIn("Removed by a mandatory condition: 1", text)
        self.assertIn("budget 1, transfers 1", text)
        self.assertIn("Offers left to rank: 3", text)

    def test_nothing_in_scope(self):
        text = text_of(format_filter(filter_offers([], REQUEST, {})))
        self.assertIn("Offers for this city and these dates: 0", text)
        self.assertIn("nothing to rank", text)

    def test_everything_removed_names_the_strictest_condition(self):
        result = filter_offers(three_offers(), make_request(budget=10), SEATS)
        text = text_of(format_filter(result))
        self.assertIn("No offer meets every mandatory condition", text)
        self.assertIn("'budget' (3)", text)

    def test_removed_offers_are_listed_with_every_reason(self):
        offers = [make_offer("B", serial=1, price=60, transfers=2)]
        result = filter_offers(offers, REQUEST, SEATS)
        text = text_of(format_removed(result))
        self.assertIn("SR-MAL-0001-B: price 60.00 EUR is above the budget of 50.00 EUR; "
                      "2 transfer(s), at most 1 allowed", text)


class RankingTextTests(unittest.TestCase):
    def setUp(self):
        self.weights = check_weights(SPEC_WEIGHTS)
        self.ranked = rank_offers(three_offers(), REQUEST, self.weights)

    def test_each_offer_shows_value_points_weight_contribution_and_total(self):
        text = text_of(format_ranking(self.ranked, self.weights, SEATS, top=0))
        self.assertIn("#1  SR-MAL-0002-S  SolRuta, standard fare   TOTAL 70.00", text)
        self.assertIn("Mon 12 Jul 10:00 -> 15:00 (5h 00m), direct, 5 seat(s) left", text)
        self.assertRegex(text, r"Price\s+40\.00 EUR\s+50\.0 pts x\s+40% =\s+20\.00")
        self.assertRegex(text, r"Flexibility\s+changeable\s+50\.0 pts x\s+20% =\s+10\.00")
        self.assertIn("#3  SR-MAL-0003-F", text)

    def test_explanation_follows_the_ranking(self):
        text = text_of(format_ranking(self.ranked, self.weights, SEATS, top=0))
        self.assertIn("Why #1 ranks first:", text)
        self.assertIn("ranks first with 70.00 points", text)

    def test_top_limits_what_is_shown_and_says_so(self):
        text = text_of(format_ranking(self.ranked, self.weights, SEATS, top=2))
        self.assertNotIn("#3 ", text)
        self.assertIn("Showing 2 of 3 ranked offers", text)

    def test_no_ranked_offers_gives_no_table(self):
        self.assertEqual(format_ranking([], self.weights, SEATS, top=10), [])

    def test_overnight_arrival_shows_the_arrival_day(self):
        departure = make_departure(departure="2027-07-12T22:30", arrival="2027-07-13T05:15")
        self.assertEqual(format_when(departure), "Mon 12 Jul 22:30 -> Tue 13 Jul 05:15")


class ComparisonTextTests(unittest.TestCase):
    def test_profiles_are_shown_side_by_side(self):
        results = {}
        for key, weights in (
            ("cheap", {"price": 70, "duration": 10, "time": 10, "flex": 10}),
            ("flexible", {"price": 10, "duration": 10, "time": 10, "flex": 70}),
        ):
            checked = check_weights(weights)
            results[key] = (f"{key} traveller", checked, rank_offers(three_offers(), REQUEST, checked))
        text = text_of(format_comparison(results, SEATS))
        self.assertRegex(text, r"cheap\s+SR-MAL-0001-B")
        self.assertRegex(text, r"flexible\s+SR-MAL-0003-F")
        self.assertIn("The 2 profiles recommend 2 different offer(s).", text)
        self.assertIn("Profile 'cheap': cheap traveller", text)
        self.assertIn("Weights: Price 70%, Duration 10%, Departure 10%, Flexibility 10%", text)
        self.assertIn("next: #2", text)


class BookingTextTests(unittest.TestCase):
    def test_confirmation_shows_reference_journey_price_and_conditions(self):
        booking = make_booking(letter="S", passengers=2)
        text = text_of(format_confirmation(booking))
        self.assertIn("BOOKING CONFIRMED", text)
        self.assertIn("Reference:      BB-20261120-AAAAAA", text)
        self.assertIn("Journey:        Madrid to Malaga with SolRuta", text)
        self.assertIn("Departure:      Mon 12 Jul 2027 08:00", text)
        self.assertIn("Offer:          SR-MAL-0001-S (standard fare)", text)
        self.assertIn("Total price:    60.00 EUR (30.00 EUR per person)", text)
        self.assertIn("Payment:        paid (simulated)", text)
        self.assertIn("changeable:   no", text)
        self.assertIn("luggage:      20 kg per person", text)
        self.assertIn("No real payment was taken", text)
        self.assertNotIn("Refund:", text)

    def test_booking_list(self):
        self.assertEqual(format_bookings([]), ["No bookings saved."])
        text = text_of(format_bookings([make_booking(), make_booking("BB-20261120-BBBBBB", status="cancelled")]))
        self.assertRegex(text, r"BB-20261120-AAAAAA\s+SR-MAL-0001-B\s+Mon 12 Jul 08:00\s+1\s+30\.00 EUR\s+confirmed")
        self.assertRegex(text, r"BB-20261120-BBBBBB.*cancelled")


if __name__ == "__main__":
    unittest.main()
