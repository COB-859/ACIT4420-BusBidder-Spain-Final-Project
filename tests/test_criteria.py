"""Tests for the scoring criteria."""

import unittest

from busbidder.criteria import (
    CORE_CRITERIA,
    CRITERIA,
    criterion,
    relative_points,
)
from busbidder.models import Company, TimeBand
from tests.helpers import make_offer, make_request

REQUEST = make_request()


def points(name, offers, request=REQUEST):
    """Return the points a criterion gives each offer."""
    return [round(score.points, 2) for score in CRITERIA[name].function(offers, request)]


class RegistryTests(unittest.TestCase):
    def test_all_criteria_are_registered(self):
        self.assertEqual(
            list(CRITERIA),
            ["price", "duration", "time", "flex", "transfers", "rating", "emissions"],
        )
        self.assertTrue(set(CORE_CRITERIA) <= set(CRITERIA))

    def test_registering_a_name_twice_is_an_error(self):
        with self.assertRaises(ValueError):
            criterion("price", "Price again")(lambda offers, request: [])


class RelativePointsTests(unittest.TestCase):
    def test_lowest_gets_100_and_highest_gets_0(self):
        self.assertEqual(relative_points([30, 40, 50], 5), [100, 50, 0])

    def test_all_equal_values_get_100(self):
        self.assertEqual(relative_points([40, 40, 40], 5), [100, 100, 100])

    def test_single_value_gets_100(self):
        self.assertEqual(relative_points([40], 5), [100])

    def test_no_values(self):
        self.assertEqual(relative_points([], 5), [])

    def test_range_below_the_minimum_is_not_stretched(self):
        # 2 euros apart with a 5 euro minimum range: 60 points, not 0.
        self.assertEqual(relative_points([30, 32], 5), [100, 60])

    def test_range_exactly_at_the_minimum(self):
        self.assertEqual(relative_points([30, 35], 5), [100, 0])

    def test_one_very_low_value_pulls_the_others_down(self):
        result = [round(point, 1) for point in relative_points([10, 40, 42, 45], 5)]
        self.assertEqual(result, [100, 14.3, 8.6, 0])


class PriceAndDurationTests(unittest.TestCase):
    def test_price(self):
        offers = [make_offer(serial=n, price=price) for n, price in enumerate((30, 40, 50), 1)]
        self.assertEqual(points("price", offers), [100, 50, 0])
        self.assertEqual(CRITERIA["price"].function(offers, REQUEST)[0].value, "30.00 EUR")

    def test_small_ranges_are_not_stretched_to_the_full_scale(self):
        # 2 euros apart with a 5 euro minimum range.
        prices = [make_offer(serial=n, price=price) for n, price in enumerate((30, 32), 1)]
        self.assertEqual(points("price", prices), [100, 60])
        # 10 minutes apart with a 30 minute minimum range.
        durations = [
            make_offer(serial=1, arrival="2027-07-12T14:00"),
            make_offer(serial=2, arrival="2027-07-12T14:10"),
        ]
        self.assertEqual(points("duration", durations), [100, 66.67])
        # 0.5 kg apart with a 1 kg minimum range.
        emissions = [make_offer(serial=1, emissions_kg=9.0), make_offer(serial=2, emissions_kg=9.5)]
        self.assertEqual(points("emissions", emissions), [100, 50])

    def test_duration(self):
        offers = [
            make_offer(serial=1, arrival="2027-07-12T13:00"),  # 5 hours
            make_offer(serial=2, arrival="2027-07-12T14:00"),  # 6 hours
            make_offer(serial=3, arrival="2027-07-12T15:00"),  # 7 hours
        ]
        self.assertEqual(points("duration", offers), [100, 50, 0])
        self.assertEqual(CRITERIA["duration"].function(offers, REQUEST)[2].value, "7h 00m")


class DepartureTimeTests(unittest.TestCase):
    def offer_at(self, clock):
        hour = int(clock[:2])
        return make_offer(departure=f"2027-07-12T{clock}", arrival=f"2027-07-13T{hour:02d}:00")

    def test_inside_the_band_and_on_its_edges(self):
        offers = [self.offer_at(clock) for clock in ("06:00", "09:30", "12:00")]
        self.assertEqual(points("time", offers), [100, 100, 100])

    def test_points_fall_steadily_outside_the_band(self):
        offers = [self.offer_at(clock) for clock in ("15:00", "05:00", "13:30")]
        self.assertEqual(points("time", offers), [50, 83.33, 75])

    def test_six_hours_outside_and_beyond_is_zero(self):
        offers = [self.offer_at(clock) for clock in ("18:00", "21:00", "23:00")]
        self.assertEqual(points("time", offers), [0, 0, 0])

    def test_exact_preferred_time(self):
        request = make_request(preferred_departure=TimeBand.from_text("08:00"))
        offers = [self.offer_at(clock) for clock in ("08:00", "11:00", "14:00")]
        self.assertEqual(points("time", offers, request), [100, 50, 0])


class FixedScaleTests(unittest.TestCase):
    def test_flexibility(self):
        offers = [
            make_offer("B"),
            make_offer("S", changeable=True),
            make_offer("S", refundable=True),
            make_offer("F", changeable=True, refundable=True),
        ]
        # make_offer gives a refundable ticket a refund of 80 percent: 50 x 0.8 = 40 points.
        self.assertEqual(points("flex", offers), [0, 50, 40, 90])
        values = [score.value for score in CRITERIA["flex"].function(offers, REQUEST)]
        self.assertEqual(values, ["fixed", "changeable", "80% refund", "changeable + 80% refund"])

    def test_flexibility_follows_the_refund_percentage(self):
        offers = [make_offer("S", changeable=True, refundable=True, refund_percent=percent)
                  for percent in (50, 90, 100)]
        self.assertEqual(points("flex", offers), [75, 95, 100])


    def test_transfers(self):
        offers = [make_offer(transfers=count) for count in (0, 1, 2, 3)]
        self.assertEqual(points("transfers", offers), [100, 50, 0, 0])

    def test_rating(self):
        offers = [make_offer(company=Company("SR", "SolRuta", rating)) for rating in (1.0, 3.0, 4.5, 5.0)]
        self.assertEqual(points("rating", offers), [0, 50, 87.5, 100])


class EmissionsTests(unittest.TestCase):
    def offers_with(self, *values):
        return [make_offer(serial=n, emissions_kg=value) for n, value in enumerate(values, 1)]

    def test_lower_emissions_get_more_points(self):
        self.assertEqual(points("emissions", self.offers_with(9.0, 12.0, 15.0)), [100, 50, 0])

    def test_missing_value_gets_the_lowest_points_of_the_others(self):
        offers = self.offers_with(9.0, None, 12.0, 15.0)
        scores = CRITERIA["emissions"].function(offers, REQUEST)
        self.assertEqual([round(score.points) for score in scores], [100, 0, 50, 0])
        self.assertTrue(scores[1].estimated)
        self.assertEqual(scores[1].value, "not reported")
        self.assertFalse(scores[0].estimated)

    def test_missing_value_never_scores_above_an_offer_that_reports(self):
        # Reported points are 100, 80 and 0: the median would be 80.
        offers = self.offers_with(9.0, 10.2, 15.0, None)
        self.assertEqual(points("emissions", offers), [100, 80, 0, 0])

    def test_missing_value_in_a_narrow_range_gets_the_lowest_points_not_zero(self):
        # The range of 0.5 kg is below the minimum range of 1 kg, so the worst reporter keeps 50.
        offers = self.offers_with(9.0, 9.5, None)
        self.assertEqual(points("emissions", offers), [100, 50, 50])

    def test_no_offer_reports_a_value(self):
        offers = self.offers_with(None, None)
        scores = CRITERIA["emissions"].function(offers, REQUEST)
        self.assertEqual([score.points for score in scores], [50, 50])
        self.assertTrue(all(score.estimated for score in scores))

    def test_only_one_offer_reports_a_value(self):
        self.assertEqual(points("emissions", self.offers_with(12.0, None)), [100, 100])


if __name__ == "__main__":
    unittest.main()
