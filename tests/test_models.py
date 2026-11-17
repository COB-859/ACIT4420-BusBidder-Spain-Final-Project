"""Tests for the domain classes."""

import unittest
from datetime import date, datetime, time

from busbidder.exceptions import InvalidIdentifierError, InvalidRecordError, InvalidRequestError
from busbidder.models import (
    Booking,
    Company,
    Departure,
    Destination,
    Fare,
    Offer,
    TimeBand,
    TravelRequest,
)
from tests.helpers import COMPANY, DESTINATION, make_departure, make_fare, make_offer, make_request


class CompanyAndDestinationTests(unittest.TestCase):
    def test_valid_company(self):
        self.assertEqual(Company("CE", "Camino Express", 4.5).rating, 4.5)

    def test_rating_boundaries_are_accepted(self):
        Company("CE", "Camino Express", 1.0)
        Company("CE", "Camino Express", 5.0)

    def test_rating_outside_scale_is_rejected(self):
        for rating in (0.9, 5.1, 7.2):
            with self.subTest(rating=rating), self.assertRaises(InvalidRecordError):
                Company("CE", "Camino Express", rating)

    def test_bad_company_code_or_name_is_rejected(self):
        for code, name in (("ce", "Camino"), ("CEX", "Camino"), ("CE", " ")):
            with self.subTest(code=code, name=name), self.assertRaises(InvalidRecordError):
                Company(code, name, 4.0)

    def test_destination_checks(self):
        Destination("MAL", "Malaga", 530)
        for code, name, distance in (("MA", "Malaga", 530), ("MAL", "", 530), ("MAL", "Malaga", 0)):
            with self.subTest(code=code), self.assertRaises(InvalidRecordError):
                Destination(code, name, distance)


class DepartureTests(unittest.TestCase):
    def test_duration_is_calculated(self):
        self.assertEqual(make_departure().duration_minutes, 360)

    def test_overnight_arrival_is_valid(self):
        departure = make_departure(departure="2027-07-12T22:30", arrival="2027-07-13T05:15")
        self.assertEqual(departure.duration_minutes, 405)

    def test_arrival_before_or_equal_to_departure_is_rejected(self):
        for arrival in ("2027-07-12T07:00", "2027-07-12T08:00"):
            with self.subTest(arrival=arrival), self.assertRaises(InvalidRecordError) as caught:
                make_departure(arrival=arrival)
            self.assertIn("is not after departure", caught.exception.reason)

    def test_journey_over_24_hours_is_rejected(self):
        make_departure(arrival="2027-07-13T08:00")
        with self.assertRaises(InvalidRecordError):
            make_departure(arrival="2027-07-13T08:01")

    def test_zero_seats_is_valid_but_negative_is_not(self):
        self.assertEqual(make_departure(seats=0).seats, 0)
        with self.assertRaises(InvalidRecordError):
            make_departure(seats=-1)

    def test_negative_transfers_and_emissions_are_rejected(self):
        with self.assertRaises(InvalidRecordError):
            make_departure(transfers=-1)
        with self.assertRaises(InvalidRecordError):
            make_departure(emissions_kg=-0.1)

    def test_missing_emissions_is_allowed(self):
        self.assertIsNone(make_departure(emissions_kg=None).emissions_kg)

    def test_id_must_match_company_and_destination(self):
        other_company = Company("CE", "Camino Express", 4.5)
        other_destination = Destination("BAR", "Barcelona", 620)
        when = {"departure": datetime(2027, 7, 12, 8), "arrival": datetime(2027, 7, 12, 14),
                "transfers": 0, "seats": 10}
        with self.assertRaises(InvalidRecordError) as caught:
            Departure("SR-MAL-0001", other_company, DESTINATION, **when)
        self.assertIn("does not match company", caught.exception.reason)
        with self.assertRaises(InvalidRecordError) as caught:
            Departure("SR-MAL-0001", COMPANY, other_destination, **when)
        self.assertIn("does not match destination", caught.exception.reason)

    def test_malformed_id_is_an_identifier_error(self):
        with self.assertRaises(InvalidIdentifierError):
            Departure("SR_MAL_1", COMPANY, DESTINATION, datetime(2027, 7, 12, 8),
                      datetime(2027, 7, 12, 14), 0, 10)


class FareTests(unittest.TestCase):
    def test_valid_fare(self):
        fare = make_fare(letter="F", price=45.0, changeable=True, refundable=True)
        self.assertEqual(fare.fare_type, "flex")

    def test_price_must_be_above_zero(self):
        for price in (0, -5.0):
            with self.subTest(price=price), self.assertRaises(InvalidRecordError):
                make_fare(price=price)

    def test_implausibly_high_price_is_rejected(self):
        make_fare(price=500.0)
        with self.assertRaises(InvalidRecordError) as caught:
            make_fare(price=3819.0)   # 38.19 typed without the decimal point
        self.assertIn("price is implausibly high", caught.exception.reason)

    def test_change_fee_on_a_ticket_that_cannot_be_changed_is_rejected(self):
        make_fare(changeable=True, change_fee=25)
        with self.assertRaises(InvalidRecordError) as caught:
            make_fare(changeable=False, change_fee=25)
        self.assertIn("changeable is no but the change fee is 25", caught.exception.reason)

    def test_offer_id_must_match_departure_and_fare_type(self):
        with self.assertRaises(InvalidRecordError) as caught:
            Fare("SR-MAL-0001-B", "SR-MAL-0002", "basic", 30, False, False, 0, 0, 20)
        self.assertIn("does not match departure", caught.exception.reason)
        with self.assertRaises(InvalidRecordError) as caught:
            Fare("SR-MAL-0001-B", "SR-MAL-0001", "flex", 30, False, False, 0, 0, 20)
        self.assertIn("does not match fare type", caught.exception.reason)

    def test_unknown_fare_type_is_rejected(self):
        with self.assertRaises(InvalidRecordError):
            Fare("SR-MAL-0001-B", "SR-MAL-0001", "luxury", 30, False, False, 0, 0, 20)

    def test_refund_percent_range(self):
        make_fare(refundable=True, refund_percent=100)
        with self.assertRaises(InvalidRecordError):
            make_fare(refundable=True, refund_percent=150)
        with self.assertRaises(InvalidRecordError):
            make_fare(refund_percent=-1)

    def test_refundable_and_refund_percent_must_agree(self):
        with self.assertRaises(InvalidRecordError):
            make_fare(refundable=False, refund_percent=80)
        with self.assertRaises(InvalidRecordError):
            make_fare(refundable=True, refund_percent=0)

    def test_negative_fee_and_luggage_are_rejected(self):
        with self.assertRaises(InvalidRecordError):
            make_fare(change_fee=-1)
        with self.assertRaises(InvalidRecordError):
            make_fare(luggage_kg=-1)

    def test_fare_survives_a_round_trip_through_plain_values(self):
        fare = make_fare(letter="S", changeable=True, change_fee=10)
        self.assertEqual(Fare.from_dict(fare.to_dict()), fare)


class OfferTests(unittest.TestCase):
    def test_offer_exposes_fare_and_departure_values(self):
        offer = make_offer(price=33.5)
        self.assertEqual(offer.offer_id, "SR-MAL-0001-B")
        self.assertEqual(offer.price, 33.5)
        self.assertEqual(offer.company.name, "SolRuta")
        self.assertEqual(offer.duration_minutes, 360)

    def test_fare_must_belong_to_the_departure(self):
        with self.assertRaises(InvalidRecordError):
            Offer(make_departure(serial=1), make_fare("SR-MAL-0002"))


class TimeBandTests(unittest.TestCase):
    def setUp(self):
        self.morning = TimeBand.from_text("morning")
        self.night = TimeBand.from_text("night")

    def test_inside_the_band_is_zero(self):
        for clock in (time(6, 0), time(9, 30), time(12, 0)):
            with self.subTest(clock=clock):
                self.assertEqual(self.morning.minutes_outside(clock), 0)

    def test_outside_the_band_is_distance_to_nearest_edge(self):
        self.assertEqual(self.morning.minutes_outside(time(15, 0)), 180)
        self.assertEqual(self.morning.minutes_outside(time(5, 0)), 60)
        self.assertEqual(self.morning.minutes_outside(time(18, 0)), 360)

    def test_distance_is_measured_around_the_clock(self):
        # 23:00 is 7 hours before 06:00 going over midnight, and 11 after 12:00.
        self.assertEqual(self.morning.minutes_outside(time(23, 0)), 420)
        self.assertEqual(self.morning.minutes_outside(time(1, 0)), 300)

    def test_band_over_midnight(self):
        for clock in (time(22, 0), time(23, 30), time(0, 0), time(6, 0)):
            with self.subTest(clock=clock):
                self.assertEqual(self.night.minutes_outside(clock), 0)
        self.assertEqual(self.night.minutes_outside(time(20, 0)), 120)
        self.assertEqual(self.night.minutes_outside(time(8, 0)), 120)

    def test_exact_time_is_a_band_of_zero_width(self):
        band = TimeBand.from_text("08:30")
        self.assertEqual(band.minutes_outside(time(8, 30)), 0)
        self.assertEqual(band.minutes_outside(time(10, 30)), 120)
        self.assertEqual(band.describe(), "08:30")

    def test_words_are_not_case_sensitive(self):
        self.assertEqual(TimeBand.from_text(" Morning "), self.morning)
        self.assertEqual(self.morning.describe(), "morning (06:00 to 12:00)")

    def test_unknown_text_is_a_request_error(self):
        for text in ("brunch", "25:00", ""):
            with self.subTest(text=text), self.assertRaises(InvalidRequestError):
                TimeBand.from_text(text)


class TravelRequestTests(unittest.TestCase):
    def test_valid_request_has_defaults(self):
        request = make_request()
        self.assertEqual(request.passengers, 1)
        self.assertIsNone(request.budget)

    def test_single_day_window_is_valid(self):
        make_request(date_from=date(2027, 7, 12), date_to=date(2027, 7, 12))

    def test_bad_requests_are_rejected(self):
        bad_options = (
            {"budget": float("nan")},
            {"budget": float("inf")},
            {"date_from": date(2027, 7, 14), "date_to": date(2027, 7, 12)},
            {"passengers": 0},
            {"budget": 0},
            {"budget": -10},
            {"max_transfers": -1},
            {"max_duration_minutes": -5},
            {"min_luggage_kg": -1},
        )
        for options in bad_options:
            with self.subTest(options=options), self.assertRaises(InvalidRequestError):
                make_request(**options)


class BookingTests(unittest.TestCase):
    def make_booking(self) -> Booking:
        offer = make_offer("F", price=45.0, changeable=True, refundable=True)
        return Booking(
            reference="BB-20261120-7K3QX9",
            passengers=2,
            total_price=90.0,
            fare=offer.fare,
            company_name=offer.company.name,
            destination_name=offer.departure.destination.name,
            departure=offer.departure.departure,
            arrival=offer.departure.arrival,
            booked_at=datetime(2026, 11, 20, 9, 15),
        )

    def test_booking_knows_its_offer_and_departure(self):
        booking = self.make_booking()
        self.assertEqual(booking.offer_id, "SR-MAL-0001-F")
        self.assertEqual(booking.departure_id, "SR-MAL-0001")
        self.assertTrue(booking.is_active)

    def test_booking_checks_its_own_values(self):
        good = self.make_booking().to_dict()
        for name, value in (("passengers", 0), ("passengers", 1.5), ("status", "maybe"),
                            ("total_price", -1.0), ("reference", "BB-1")):
            with self.subTest(name=name, value=value), self.assertRaises(InvalidRecordError):
                Booking.from_dict({**good, name: value})

    def test_cancelled_booking_is_not_active(self):
        booking = self.make_booking()
        booking.status = "cancelled"
        self.assertFalse(booking.is_active)

    def test_booking_survives_a_round_trip_through_plain_values(self):
        booking = self.make_booking()
        self.assertEqual(Booking.from_dict(booking.to_dict()), booking)

    def test_saved_booking_keeps_the_fare_conditions(self):
        saved = self.make_booking().to_dict()
        self.assertEqual(saved["fare"]["refund_percent"], 80.0)
        self.assertTrue(saved["fare"]["changeable"])


if __name__ == "__main__":
    unittest.main()
