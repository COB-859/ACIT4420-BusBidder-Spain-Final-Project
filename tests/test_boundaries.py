"""Tests at exact limits, added after fault injection.

tools/fault_injection.py changes one operator at a time and runs the
tests. The changes below were not noticed by any test, so each test here
sits exactly on the limit that the change would move.
"""

import tempfile
import unittest
from dataclasses import replace
from datetime import time
from pathlib import Path

from busbidder.booking import BookingStore
from busbidder.criteria import transfers_score
from busbidder.explain import DISADVANTAGE_BELOW_POINTS, explain_top
from busbidder.loader import write_rejected
from busbidder.models import TimeBand
from busbidder.ranking import check_weights, near_tie_gap, rank_offers
from tests.helpers import make_departure, make_offer, make_request
from tests.test_booking import make_booking
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


if __name__ == "__main__":
    unittest.main()
