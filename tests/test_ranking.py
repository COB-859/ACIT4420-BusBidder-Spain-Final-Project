"""Tests for the weight checks and the ranking."""

import unittest

from busbidder.exceptions import WeightError
from busbidder.ranking import check_weights, dominant_criterion, parse_weights, rank_offers
from tests.helpers import make_offer, make_request

SPEC_WEIGHTS = {"price": 40, "duration": 25, "time": 15, "flex": 20}
EQUAL_WEIGHTS = {"price": 25, "duration": 25, "time": 25, "flex": 25}
REQUEST = make_request()


def three_offers():
    """Three offers whose ranking is worked out by hand in the test below."""
    return [
        make_offer("B", serial=1, price=30, departure="2027-07-12T08:00", arrival="2027-07-12T14:00"),
        make_offer("S", serial=2, price=40, changeable=True,
                   departure="2027-07-12T10:00", arrival="2027-07-12T15:00"),
        make_offer("F", serial=3, price=50, changeable=True, refundable=True,
                   departure="2027-07-12T15:00", arrival="2027-07-12T22:00"),
    ]


class CheckWeightsTests(unittest.TestCase):
    def test_percentages_that_total_100_are_accepted(self):
        self.assertEqual(check_weights(SPEC_WEIGHTS), SPEC_WEIGHTS)

    def test_fractions_that_total_1_are_turned_into_percentages(self):
        result = check_weights({"price": 0.4, "duration": 0.25, "time": 0.15, "flex": 0.2})
        self.assertEqual({name: round(weight, 6) for name, weight in result.items()}, SPEC_WEIGHTS)

    def test_total_within_tolerance_is_accepted(self):
        check_weights({"price": 33.33, "duration": 33.33, "time": 16.67, "flex": 16.67})
        check_weights({"price": 40.005, "duration": 25, "time": 15, "flex": 20})

    def test_wrong_total_is_rejected_and_not_rescaled(self):
        for weights in (
            {"price": 40, "duration": 25, "time": 15, "flex": 10},   # 90
            {"price": 40, "duration": 25, "time": 15, "flex": 20.5},  # 100.5
            {"price": 4, "duration": 2, "time": 2, "flex": 2},        # 10
        ):
            with self.subTest(weights=weights), self.assertRaises(WeightError) as caught:
                check_weights(weights)
            self.assertIn("must total 100", str(caught.exception))

    def test_negative_weight_is_rejected(self):
        with self.assertRaises(WeightError) as caught:
            check_weights({"price": 60, "duration": 30, "time": 20, "flex": -10})
        self.assertIn("negative", str(caught.exception))

    def test_unknown_criterion_is_rejected(self):
        with self.assertRaises(WeightError) as caught:
            check_weights({**SPEC_WEIGHTS, "comfort": 0})
        self.assertIn("unknown criterion: comfort", str(caught.exception))

    def test_not_a_number_is_rejected(self):
        for value in ("40", None, float("nan"), float("inf"), True, 10 ** 400):
            with self.subTest(value=str(value)[:12]), self.assertRaises(WeightError) as caught:
                check_weights({"price": value, "duration": 25, "time": 15, "flex": 20})
            self.assertIn("weight for price is not a usable number", str(caught.exception))

    def test_fractions_must_total_one(self):
        for flex in (0.1, 0.3):   # totals 0.9 and 1.1
            with self.subTest(flex=flex), self.assertRaises(WeightError):
                check_weights({"price": 0.4, "duration": 0.25, "time": 0.15, "flex": flex})

    def test_each_core_criterion_needs_a_weight_above_zero(self):
        full = {"price": 25, "duration": 25, "time": 25, "flex": 25}
        for name in full:
            # Move the weight to an optional criterion so the total stays 100.
            weights = {**full, name: 0, "transfers": 25}
            with self.subTest(name=name), self.assertRaises(WeightError) as caught:
                check_weights(weights)
            self.assertIn(f"no weight above 0 for: {name}", str(caught.exception))

    def test_core_criterion_with_zero_or_no_weight_is_rejected(self):
        for weights in (
            {"price": 100},
            {"price": 50, "duration": 30, "time": 20, "flex": 0},
            {"price": 50, "duration": 30, "time": 20},
        ):
            with self.subTest(weights=weights), self.assertRaises(WeightError) as caught:
                check_weights(weights)
            self.assertIn("no weight above 0 for", str(caught.exception))

    def test_empty_weights_are_rejected(self):
        with self.assertRaises(WeightError):
            check_weights({})

    def test_optional_criterion_with_weight_zero_is_left_out(self):
        result = check_weights({**SPEC_WEIGHTS, "rating": 0})
        self.assertNotIn("rating", result)

    def test_dominant_criterion(self):
        self.assertIsNone(dominant_criterion(SPEC_WEIGHTS))
        self.assertIsNone(dominant_criterion({"price": 70, "duration": 10, "time": 10, "flex": 10}))
        self.assertEqual(
            dominant_criterion({"price": 71, "duration": 10, "time": 10, "flex": 9}), "price"
        )


class ParseWeightsTests(unittest.TestCase):
    def test_normal_text(self):
        self.assertEqual(parse_weights("price=40, duration=25,time=15,FLEX=20"), SPEC_WEIGHTS)

    def test_bad_text_is_rejected(self):
        for text in ("price", "price=", "price=lots", "=40", "price=40,price=60", ""):
            with self.subTest(text=text), self.assertRaises(WeightError):
                parse_weights(text)


class RankOffersTests(unittest.TestCase):
    def test_hand_calculated_ranking(self):
        # Points   price  duration  time  flex   total with 40/25/15/20
        # offer 1   100      50     100     0    40 + 12.5 + 15 + 0  = 67.5
        # offer 2    50     100     100    50    20 + 25 + 15 + 10   = 70.0
        # offer 3     0       0      50   100     0 + 0 + 7.5 + 20   = 27.5
        ranked = rank_offers(three_offers(), REQUEST, SPEC_WEIGHTS)
        self.assertEqual(
            [(scored.offer.offer_id, scored.total) for scored in ranked],
            [("SR-MAL-0002-S", 70.0), ("SR-MAL-0001-B", 67.5), ("SR-MAL-0003-F", 27.5)],
        )
        best = ranked[0]
        self.assertEqual({name: score.points for name, score in best.scores.items()},
                         {"price": 50, "duration": 100, "time": 100, "flex": 50})
        self.assertEqual(best.contributions, {"price": 20, "duration": 25, "time": 15, "flex": 10})

    def test_other_weights_give_another_winner_for_the_same_offers(self):
        price_first = {"price": 70, "duration": 10, "time": 10, "flex": 10}
        flex_first = {"price": 10, "duration": 10, "time": 10, "flex": 70}
        self.assertEqual(rank_offers(three_offers(), REQUEST, price_first)[0].offer.offer_id,
                         "SR-MAL-0001-B")
        self.assertEqual(rank_offers(three_offers(), REQUEST, flex_first)[0].offer.offer_id,
                         "SR-MAL-0003-F")

    def test_fractions_give_the_same_ranking_as_percentages(self):
        fractions = {name: weight / 100 for name, weight in SPEC_WEIGHTS.items()}
        by_fraction = rank_offers(three_offers(), REQUEST, fractions)
        by_percent = rank_offers(three_offers(), REQUEST, SPEC_WEIGHTS)
        self.assertEqual([s.total for s in by_fraction], [s.total for s in by_percent])

    def test_ranking_is_the_same_whatever_the_input_order(self):
        forward = rank_offers(three_offers(), REQUEST, SPEC_WEIGHTS)
        backward = rank_offers(list(reversed(three_offers())), REQUEST, SPEC_WEIGHTS)
        self.assertEqual([s.offer.offer_id for s in forward], [s.offer.offer_id for s in backward])

    def test_total_stays_between_0_and_100(self):
        for scored in rank_offers(three_offers(), REQUEST, SPEC_WEIGHTS):
            self.assertTrue(0 <= scored.total <= 100)

    def test_no_offers_gives_an_empty_ranking(self):
        self.assertEqual(rank_offers([], REQUEST, SPEC_WEIGHTS), [])

    def test_single_offer_scores_100_on_the_relative_criteria(self):
        ranked = rank_offers([make_offer(price=49.99)], make_request(budget=50), SPEC_WEIGHTS)
        self.assertEqual(ranked[0].scores["price"].points, 100)
        self.assertEqual(ranked[0].scores["duration"].points, 100)

    def test_weights_are_checked_before_ranking(self):
        with self.assertRaises(WeightError):
            rank_offers(three_offers(), REQUEST, {"price": 100})

    def test_criteria_with_weight_zero_are_not_calculated(self):
        ranked = rank_offers(three_offers(), REQUEST, {**SPEC_WEIGHTS, "emissions": 0})
        self.assertNotIn("emissions", ranked[0].scores)

    def test_optional_criterion_changes_the_total_when_weighted(self):
        offers = [make_offer(serial=1, transfers=0), make_offer(serial=2, transfers=2)]
        weights = {"price": 20, "duration": 20, "time": 20, "flex": 20, "transfers": 20}
        ranked = rank_offers(offers, REQUEST, weights)
        self.assertEqual([s.offer.offer_id for s in ranked], ["SR-MAL-0001-B", "SR-MAL-0002-B"])
        self.assertEqual(ranked[0].total - ranked[1].total, 20)


class TieTests(unittest.TestCase):
    def test_equal_totals_are_ordered_by_lower_price(self):
        # Cheap and fixed against 5 euros dearer and fully flexible: 75 points each.
        cheap = make_offer("B", serial=2, price=30)
        flexible = make_offer("F", serial=1, price=35, changeable=True, refundable=True)
        ranked = rank_offers([flexible, cheap], REQUEST, EQUAL_WEIGHTS)
        self.assertEqual([s.total for s in ranked], [75.0, 75.0])
        self.assertEqual([s.offer.offer_id for s in ranked], ["SR-MAL-0002-B", "SR-MAL-0001-F"])

    def test_tie_is_decided_on_the_total_rounded_to_two_decimals(self):
        # Weights price 25, duration 10, time 40, flex 25. A third offer sets the ranges
        # (price 40 to 140, duration 399 to 899 minutes).
        # cheap:  price 100.00 pts, duration 99.8 pts -> 25 + 9.98   + 40 = 74.98
        # faster: price  99.93 pts, duration 100 pts  -> 24.9825 + 10 + 40 = 74.9825
        # Unrounded, the faster offer is ahead. Rounded, both have 74.98 and price decides.
        weights = {"price": 25, "duration": 10, "time": 40, "flex": 25}
        cheap = make_offer(serial=1, price=40.00, arrival="2027-07-12T14:40")
        faster = make_offer(serial=2, price=40.07, arrival="2027-07-12T14:39")
        slow = make_offer(serial=3, price=140.00, arrival="2027-07-12T22:59")
        ranked = rank_offers([faster, slow, cheap], REQUEST, weights)
        self.assertEqual([(s.offer.offer_id, s.total) for s in ranked[:2]],
                         [("SR-MAL-0001-B", 74.98), ("SR-MAL-0002-B", 74.98)])

    def test_equal_totals_and_prices_are_ordered_by_earlier_departure(self):
        later = make_offer(serial=1, departure="2027-07-12T09:00", arrival="2027-07-12T15:00")
        earlier = make_offer(serial=2, departure="2027-07-12T07:00", arrival="2027-07-12T13:00")
        ranked = rank_offers([later, earlier], REQUEST, EQUAL_WEIGHTS)
        self.assertEqual([s.offer.offer_id for s in ranked], ["SR-MAL-0002-B", "SR-MAL-0001-B"])

    def test_fully_equal_offers_are_ordered_by_offer_id(self):
        ranked = rank_offers([make_offer(serial=3), make_offer(serial=1), make_offer(serial=2)],
                             REQUEST, EQUAL_WEIGHTS)
        self.assertEqual([s.offer.offer_id for s in ranked],
                         ["SR-MAL-0001-B", "SR-MAL-0002-B", "SR-MAL-0003-B"])


if __name__ == "__main__":
    unittest.main()
