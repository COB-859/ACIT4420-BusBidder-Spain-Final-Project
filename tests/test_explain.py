"""Tests for the generated explanation."""

import unittest

from busbidder.explain import CLOSING, explain_top
from busbidder.ranking import rank_offers
from tests.helpers import make_offer, make_request
from tests.test_ranking import EQUAL_WEIGHTS, SPEC_WEIGHTS, three_offers

REQUEST = make_request()


def explanation(offers, weights=SPEC_WEIGHTS, request=REQUEST) -> str:
    """Return the explanation for the top offer as one text."""
    return "\n".join(explain_top(rank_offers(offers, request, weights)))


class ExplainTests(unittest.TestCase):
    def test_no_offers_gives_no_explanation(self):
        self.assertEqual(explain_top([]), [])

    def test_names_the_top_offer_and_its_total(self):
        text = explanation(three_offers())
        self.assertIn("SR-MAL-0002-S (SolRuta, standard fare) ranks first with 70.00 points", text)

    def test_advantages_are_the_largest_gains_over_the_runner_up(self):
        # Top (offer 2) against runner-up (offer 1): duration +12.5, flexibility +10, price -20.
        text = explanation(three_offers())
        self.assertIn("it gains most on duration (5h 00m against 6h 00m, +12.50 points) "
                      "and flexibility (changeable against fixed, +10.00 points)", text)

    def test_names_where_the_runner_up_is_better(self):
        text = explanation(three_offers())
        self.assertIn("The runner-up is better on price (30.00 EUR against 40.00 EUR, "
                      "-20.00 points for the top offer)", text)

    def test_names_the_weakest_point_as_a_disadvantage(self):
        # Offer 2 has 50 points on both price and flexibility; price comes first.
        text = explanation(three_offers())
        self.assertIn("Remaining disadvantage: price (40.00 EUR scores 50.0 of 100)", text)

    def test_names_the_largest_contributions_among_the_strong_criteria(self):
        # Offer 2 contributions: duration 25, price 20, departure 15, flexibility 10.
        # Price has only 50 points, so it is not named as a strength.
        text = explanation(three_offers())
        self.assertIn("Its largest contributions come from duration (5h 00m, 25.00 points) "
                      "and departure (10:00, 15.00 points).", text)

    def test_a_criterion_is_never_both_a_strength_and_the_disadvantage(self):
        text = explanation(three_offers())
        strengths = next(line for line in text.splitlines() if line.startswith("Its largest"))
        self.assertIn("Remaining disadvantage: price", text)
        self.assertNotIn("price", strengths)

    def test_a_weak_criterion_with_a_large_weight_is_not_named_as_a_strength(self):
        # Departure time carries 49% of the weight, but 15:30 is far outside the morning
        # (41.7 points). Its contribution is the largest, yet it is not a strength.
        offers = [
            make_offer(serial=1, price=30, departure="2027-07-12T15:30", arrival="2027-07-12T21:30"),
            make_offer(serial=2, price=30, departure="2027-07-12T16:00", arrival="2027-07-12T22:00"),
        ]
        weights = {"price": 1, "duration": 1, "time": 49, "flex": 49}
        text = explanation(offers, weights=weights)
        self.assertIn("Its largest contributions come from price", text)
        self.assertNotIn("departure (15:30", text.split("Its largest")[1].splitlines()[0])

    def test_never_claims_to_be_objectively_best(self):
        text = explanation(three_offers())
        self.assertTrue(text.endswith(CLOSING))
        self.assertNotIn("best offer", text.lower())

    def test_single_offer_claims_no_advantage(self):
        text = explanation([make_offer(price=49.99)], request=make_request(budget=50))
        self.assertIn("only offer that meets the mandatory conditions", text)
        self.assertNotIn("gains most", text)
        self.assertIn("Remaining disadvantage: flexibility (fixed scores 0.0 of 100)", text)

    def test_no_disadvantage_is_named_when_every_score_is_high(self):
        offers = [
            make_offer("F", serial=1, price=30, changeable=True, refundable=True),
            make_offer("B", serial=2, price=45),
        ]
        text = explanation(offers)
        self.assertIn("no clear disadvantage among the scored criteria", text)
        self.assertNotIn("Remaining disadvantage", text)

    def test_tie_is_explained_by_the_tie_rule(self):
        cheap = make_offer("B", serial=2, price=30)
        flexible = make_offer("F", serial=1, price=35, changeable=True, refundable=True)
        text = explanation([flexible, cheap], weights=EQUAL_WEIGHTS)
        self.assertIn("same total as the runner-up SR-MAL-0001-F (75.00 points)", text)
        self.assertIn("tie rule", text)
        self.assertIn("it gains most on price", text)

    def test_identical_offers_tie_without_any_advantage(self):
        text = explanation([make_offer(serial=1), make_offer(serial=2)], weights=EQUAL_WEIGHTS)
        self.assertIn("tie rule", text)
        self.assertNotIn("gains most", text)
        self.assertNotIn("runner-up is better", text)

    def test_criteria_without_weight_are_never_mentioned(self):
        text = explanation(three_offers())
        for word in ("rating", "emissions", "transfers"):
            self.assertNotIn(word, text)

    def test_estimated_emissions_are_pointed_out(self):
        weights = {"price": 20, "duration": 20, "time": 10, "flex": 10, "emissions": 40}
        offers = [
            make_offer(serial=1, price=30, emissions_kg=None),
            make_offer(serial=2, price=40, emissions_kg=12.0),
        ]
        text = explanation(offers, weights=weights)
        self.assertIn("SR-MAL-0001-B", text.splitlines()[0])
        self.assertIn("emissions value is not reported", text)


    def test_neutral_points_are_explained_when_no_offer_reports_emissions(self):
        weights = {"price": 20, "duration": 20, "time": 10, "flex": 10, "emissions": 40}
        offers = [
            make_offer(serial=1, price=30, emissions_kg=None),
            make_offer(serial=2, price=40, emissions_kg=None),
        ]
        text = explanation(offers, weights=weights)
        self.assertIn("No ranked offer reports its emissions, so every offer gets "
                      "the same neutral 50 points for it.", text)
        self.assertNotIn("median", text)


if __name__ == "__main__":
    unittest.main()
