"""Tests for the hard constraints and the filter."""

import unittest
from datetime import date

from busbidder.constraints import (
    CONSTRAINTS,
    constraint,
    describe_constraints,
    filter_offers,
    in_scope,
)
from busbidder.models import Destination
from tests.helpers import make_offer, make_request

SEATS = {"SR-MAL-0001": 40, "SR-MAL-0002": 40, "SR-MAL-0003": 40}


def run_filter(offers, seats=None, **request_options):
    """Filter offers for a Malaga request with the given options."""
    return filter_offers(offers, make_request(**request_options), SEATS if seats is None else seats)


class RegistryTests(unittest.TestCase):
    def test_all_constraints_are_registered_in_order(self):
        self.assertEqual(
            list(CONSTRAINTS),
            ["seats", "budget", "transfers", "duration", "changeable", "refundable", "luggage"],
        )

    def test_registering_a_name_twice_is_an_error(self):
        with self.assertRaises(ValueError):
            constraint("budget", describe=lambda request: None)(lambda offer, request, seats_left: None)

    def test_a_new_constraint_is_used_by_the_filter_without_other_changes(self):
        @constraint("test_only_basic", describe=lambda request: "no basic fares")
        def no_basic_fares(offer, request, seats_left):
            return "basic fare" if offer.fare.fare_type == "basic" else None

        self.addCleanup(CONSTRAINTS.pop, "test_only_basic")
        result = run_filter([make_offer("B"), make_offer("S", changeable=True)])
        self.assertEqual([offer.offer_id for offer in result.accepted], ["SR-MAL-0001-S"])
        self.assertEqual(result.counts["test_only_basic"], 1)
        # The same registration also puts it in the list shown to the traveller.
        self.assertEqual(describe_constraints(make_request())[-1], "no basic fares")

    def test_every_constraint_has_a_check_and_a_description(self):
        for name, rule in CONSTRAINTS.items():
            with self.subTest(name=name):
                self.assertEqual(rule.name, name)
                self.assertTrue(callable(rule.check))
                self.assertTrue(callable(rule.describe))


class ScopeTests(unittest.TestCase):
    def test_other_destination_is_out_of_scope(self):
        toledo = Destination("TOL", "Toledo", 75)
        offer = make_offer(destination=toledo)
        self.assertFalse(in_scope(offer, make_request()))

    def test_window_includes_first_and_last_day(self):
        request = make_request()
        for day, expected in (("11", False), ("12", True), ("14", True), ("15", False)):
            offer = make_offer(departure=f"2027-07-{day}T08:00", arrival=f"2027-07-{day}T14:00")
            with self.subTest(day=day):
                self.assertEqual(in_scope(offer, request), expected)

    def test_departure_on_last_day_arriving_next_day_is_in_scope(self):
        offer = make_offer(departure="2027-07-14T23:00", arrival="2027-07-15T06:00")
        self.assertTrue(in_scope(offer, make_request()))

    def test_out_of_scope_offers_are_not_counted(self):
        offer = make_offer(departure="2027-07-20T08:00", arrival="2027-07-20T14:00")
        result = run_filter([offer], budget=1)
        self.assertEqual(result.in_scope, 0)
        self.assertEqual(result.removed, [])
        self.assertEqual(sum(result.counts.values()), 0)


class ConstraintTests(unittest.TestCase):
    def assert_removed(self, result, name, text):
        self.assertEqual(result.accepted, [])
        self.assertIn(text, result.removed[0].reasons[name])

    def test_offer_with_no_limits_set_is_accepted(self):
        result = run_filter([make_offer()])
        self.assertEqual(len(result.accepted), 1)
        self.assertEqual(result.in_scope, 1)

    def test_budget_boundary(self):
        self.assertEqual(len(run_filter([make_offer(price=50.0)], budget=50).accepted), 1)
        self.assert_removed(run_filter([make_offer(price=50.01)], budget=50), "budget", "above the budget")

    def test_seats_boundary(self):
        offer = make_offer()
        self.assertEqual(len(run_filter([offer], seats={"SR-MAL-0001": 2}, passengers=2).accepted), 1)
        self.assert_removed(run_filter([offer], seats={"SR-MAL-0001": 1}, passengers=2),
                            "seats", "1 seat left, 2 needed")

    def test_sold_out_departure_is_removed(self):
        self.assert_removed(run_filter([make_offer()], seats={"SR-MAL-0001": 0}), "seats", "sold out")

    def test_departure_missing_from_the_seat_counts_is_treated_as_sold_out(self):
        self.assert_removed(run_filter([make_offer()], seats={}), "seats", "sold out")

    def test_transfers_boundary(self):
        self.assertEqual(len(run_filter([make_offer(transfers=1)], max_transfers=1).accepted), 1)
        self.assert_removed(run_filter([make_offer(transfers=2)], max_transfers=1),
                            "transfers", "2 transfers, at most 1 allowed")

    def test_duration_boundary(self):
        offer = make_offer()  # 6 hours
        self.assertEqual(len(run_filter([offer], max_duration_minutes=360).accepted), 1)
        self.assert_removed(run_filter([offer], max_duration_minutes=359), "duration", "6h 00m")

    def test_changeable_and_refundable_requirements(self):
        fixed = make_offer("B")
        self.assert_removed(run_filter([fixed], require_changeable=True), "changeable", "cannot be changed")
        self.assert_removed(run_filter([fixed], require_refundable=True), "refundable", "cannot be refunded")
        flexible = make_offer("F", changeable=True, refundable=True)
        result = run_filter([flexible], require_changeable=True, require_refundable=True)
        self.assertEqual(len(result.accepted), 1)

    def test_luggage_boundary(self):
        offer = make_offer(luggage_kg=20)
        self.assertEqual(len(run_filter([offer], min_luggage_kg=20).accepted), 1)
        self.assert_removed(run_filter([offer], min_luggage_kg=21), "luggage", "below the required 21 kg")


class FilterResultTests(unittest.TestCase):
    def setUp(self):
        self.offers = [
            make_offer("B", serial=1, price=20, transfers=2),
            make_offer("B", serial=2, price=60, transfers=2),
            make_offer("S", serial=3, price=40, changeable=True),
        ]

    def test_every_offer_is_tested_against_every_constraint(self):
        result = run_filter(self.offers, budget=50, max_transfers=1)
        self.assertEqual([offer.offer_id for offer in result.accepted], ["SR-MAL-0003-S"])
        self.assertEqual(result.counts["transfers"], 2)
        self.assertEqual(result.counts["budget"], 1)
        both = [removed for removed in result.removed if len(removed.reasons) == 2]
        self.assertEqual([removed.offer.offer_id for removed in both], ["SR-MAL-0002-B"])

    def test_strictest_names_the_constraint_that_removed_the_most(self):
        result = run_filter(self.offers, budget=50, max_transfers=1)
        self.assertEqual(result.strictest(), "transfers")

    def test_strictest_is_none_when_nothing_was_removed(self):
        self.assertIsNone(run_filter(self.offers).strictest())

    def test_no_offers_at_all(self):
        result = run_filter([])
        self.assertEqual((result.in_scope, result.accepted, result.removed), (0, [], []))
        self.assertIsNone(result.strictest())

    def test_all_offers_removed(self):
        result = run_filter(self.offers, budget=10)
        self.assertEqual(result.accepted, [])
        self.assertEqual(result.strictest(), "budget")


class DescribeTests(unittest.TestCase):
    def test_only_the_seat_condition_by_default(self):
        self.assertEqual(describe_constraints(make_request()), ["at least 1 seat left"])

    def test_every_set_condition_is_listed(self):
        request = make_request(
            passengers=2, budget=50, max_transfers=1, max_duration_minutes=480,
            require_changeable=True, require_refundable=True, min_luggage_kg=20,
            date_from=date(2027, 7, 12), date_to=date(2027, 7, 14),
        )
        self.assertEqual(describe_constraints(request), [
            "at least 2 seats left",
            "price per person at most 50.00 EUR",
            "at most 1 transfer",
            "journey at most 8h 00m",
            "ticket must be changeable",
            "ticket must be refundable",
            "luggage allowance at least 20 kg",
        ])


if __name__ == "__main__":
    unittest.main()
