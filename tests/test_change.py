"""Tests for changing a booking to another bus or fare."""

import unittest
from datetime import datetime

from busbidder.cli import EXIT_ERROR, EXIT_OK
from busbidder.exceptions import BookingError, BookingNotFoundError, NoSeatsError, NotChangeableError
from busbidder.models import Company, Destination
from tests.helpers import make_departure, make_fare
from tests.test_booking import ServiceTestCase
from tests.test_cli import CliTestCase

TOLEDO = Destination(code="TOL", name="Toledo", distance_km=75)
CAMINO = Company(code="CE", name="Camino Express", rating=4.5)


class ChangeTests(ServiceTestCase):
    def setUp(self):
        super().setUp()
        self.departures["SR-MAL-0003"] = make_departure(
            serial=3, seats=2, departure="2027-07-13T10:00", arrival="2027-07-13T16:00")
        self.departures["SR-TOL-0001"] = make_departure(serial=1, destination=TOLEDO)
        self.fares.update({
            "SR-MAL-0001-S": make_fare("SR-MAL-0001", "S", price=30.0, changeable=True, change_fee=10.0),
            "SR-MAL-0003-S": make_fare("SR-MAL-0003", "S", price=34.0, changeable=True, change_fee=7.0),
            "SR-MAL-0003-B": make_fare("SR-MAL-0003", "B", price=20.0),
            "SR-MAL-0002-S": make_fare("SR-MAL-0002", "S", price=30.0, changeable=True),
            "SR-TOL-0001-S": make_fare("SR-TOL-0001", "S", price=9.0, changeable=True),
        })
        self.service = self.make_service()

    def test_change_moves_the_booking_and_keeps_the_reference(self):
        booking = self.service.book("SR-MAL-0001-S", 2)
        change = self.service.change(booking.reference, "SR-MAL-0003-S")
        saved = self.store.load()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].reference, booking.reference)
        self.assertEqual(saved[0].offer_id, "SR-MAL-0003-S")
        self.assertEqual(saved[0].departure, datetime(2027, 7, 13, 10, 0))
        self.assertEqual(saved[0].arrival, datetime(2027, 7, 13, 16, 0))
        self.assertEqual(saved[0].total_price, 68.0)
        self.assertEqual(change.old_offer_id, "SR-MAL-0001-S")

    def test_the_fee_is_per_passenger_and_the_higher_price_is_added(self):
        booking = self.service.book("SR-MAL-0001-S", 2)             # 60.00 EUR, fee 10.00 each
        change = self.service.change(booking.reference, "SR-MAL-0003-S")   # 68.00 EUR
        self.assertEqual(change.fee, 20.0)
        self.assertEqual(change.price_difference, 8.0)
        self.assertEqual(change.to_pay, 28.0)

    def test_the_fee_follows_the_conditions_stored_with_the_booking(self):
        booking = self.service.book("SR-MAL-0001-S", 1)
        self.fares["SR-MAL-0001-S"] = make_fare("SR-MAL-0001", "S", price=30.0, changeable=True, change_fee=99.0)
        self.assertEqual(self.make_service().change(booking.reference, "SR-MAL-0003-S").fee, 10.0)

    def test_a_cheaper_ticket_gives_no_money_back(self):
        booking = self.service.book("SR-MAL-0001-S", 1)             # 30.00 EUR
        change = self.service.change(booking.reference, "SR-MAL-0003-B")   # 20.00 EUR
        self.assertEqual(change.price_difference, 0.0)
        self.assertEqual(change.to_pay, 10.0)
        self.assertEqual(change.booking.total_price, 20.0)

    def test_a_free_change_to_the_same_price_costs_nothing(self):
        booking = self.service.book("SR-MAL-0001-F", 1)             # flex, no fee
        self.fares["SR-MAL-0003-F"] = make_fare("SR-MAL-0003", "F", price=45.5, changeable=True)
        self.assertEqual(self.make_service().change(booking.reference, "SR-MAL-0003-F").to_pay, 0.0)

    def test_change_frees_the_old_seats_and_takes_the_new_ones(self):
        booking = self.service.book("SR-MAL-0001-S", 2)
        self.service.change(booking.reference, "SR-MAL-0003-S")
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 3)
        self.assertEqual(self.service.seats_left("SR-MAL-0003"), 0)

    def test_the_new_bus_may_be_filled_exactly(self):
        booking = self.service.book("SR-MAL-0001-S", 2)             # the new bus has 2 seats
        self.assertEqual(self.service.change(booking.reference, "SR-MAL-0003-S").booking.passengers, 2)

    def test_a_new_bus_without_enough_seats_leaves_the_booking_as_it_was(self):
        booking = self.service.book("SR-MAL-0001-S", 3)
        with self.assertRaises(NoSeatsError) as caught:
            self.service.change(booking.reference, "SR-MAL-0003-S")
        self.assertIn("the new bus has 2 seats left, but your booking is for 3", str(caught.exception))
        self.assertIn("Your booking is unchanged.", str(caught.exception))
        self.assertEqual(self.store.load()[0].offer_id, "SR-MAL-0001-S")

    def test_another_fare_on_the_same_bus_can_use_the_bookings_own_seats(self):
        booking = self.service.book("SR-MAL-0001-S", 3)             # the bus is now full
        change = self.service.change(booking.reference, "SR-MAL-0001-F")
        self.assertEqual(change.booking.offer_id, "SR-MAL-0001-F")
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 0)

    def test_a_ticket_that_is_not_changeable_is_refused(self):
        booking = self.service.book("SR-MAL-0001-B", 1)
        with self.assertRaises(NotChangeableError) as caught:
            self.service.change(booking.reference, "SR-MAL-0003-S")
        self.assertIn("this basic ticket cannot be changed", str(caught.exception))
        self.assertEqual(self.store.load()[0].offer_id, "SR-MAL-0001-B")

    def test_another_destination_is_refused(self):
        booking = self.service.book("SR-MAL-0001-S", 1)
        with self.assertRaises(NotChangeableError) as caught:
            self.service.change(booking.reference, "SR-TOL-0001-S")
        self.assertIn("only be changed to another SolRuta bus to Malaga", str(caught.exception))

    def test_another_company_is_refused(self):
        self.departures["CE-MAL-0001"] = make_departure(serial=1, company=CAMINO)
        self.fares["CE-MAL-0001-S"] = make_fare("CE-MAL-0001", "S", changeable=True)
        service = self.make_service()
        booking = service.book("SR-MAL-0001-S", 1)
        with self.assertRaises(NotChangeableError) as caught:
            service.change(booking.reference, "CE-MAL-0001-S")
        self.assertIn("Offer CE-MAL-0001-S is Camino Express to Malaga.", str(caught.exception))

    def test_the_same_offer_is_refused(self):
        booking = self.service.book("SR-MAL-0001-S", 1)
        with self.assertRaises(BookingError) as caught:
            self.service.change(booking.reference, "sr-mal-0001-s")
        self.assertIn("is already for offer SR-MAL-0001-S", str(caught.exception))

    def test_a_cancelled_booking_cannot_be_changed(self):
        booking = self.service.book("SR-MAL-0001-S", 1)
        self.service.cancel(booking.reference)
        with self.assertRaises(BookingError) as caught:
            self.service.change(booking.reference, "SR-MAL-0003-S")
        self.assertIn("is cancelled, so it cannot be changed", str(caught.exception))

    def test_unknown_offer_reference_and_form_are_refused(self):
        booking = self.service.book("SR-MAL-0001-S", 1)
        with self.assertRaises(BookingError) as caught:
            self.service.change(booking.reference, "NOPE")
        self.assertIn("so nothing was changed", str(caught.exception))
        with self.assertRaises(BookingNotFoundError):
            self.service.change("BB-20261120-ZZZZZZ", "SR-MAL-0003-S")
        with self.assertRaises(BookingError) as caught:
            self.service.change("12345", "SR-MAL-0003-S")
        self.assertIn("must look like BB-20261120-7K3QX9", str(caught.exception))

    def test_the_lock_is_released_after_a_change_and_after_a_refusal(self):
        booking = self.service.book("SR-MAL-0001-B", 1)
        with self.assertRaises(NotChangeableError):
            self.service.change(booking.reference, "SR-MAL-0003-S")
        self.assertFalse(self.store.lock_path.exists())

    def test_a_refund_after_a_change_follows_the_new_ticket(self):
        booking = self.service.book("SR-MAL-0001-S", 1)             # not refundable
        self.service.change(booking.reference, "SR-MAL-0001-F")    # 45.50 EUR, 80% refundable
        self.assertEqual(self.service.cancel(booking.reference).refund_amount, 36.4)


class ChangeCommandTests(CliTestCase):
    def book(self, offer: str, passengers: str = "1") -> str:
        """Book an offer and return its reference."""
        _code, text = self.run_main("book", "--offer", offer, "--passengers", passengers)
        return next(line.split()[-1] for line in text.splitlines() if line.startswith("Reference:"))

    def test_change_says_what_happened_and_what_it_costs(self):
        reference = self.book("CE-MAL-0001-S", "2")                 # 96.00 EUR, fee 10.00 each
        code, text = self.run_main("change", "--ref", reference, "--offer", "CE-MAL-0001-F")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("BOOKING CHANGED", text)
        self.assertIn(f"Booking {reference} is changed: 2 flex seats to Malaga, now leaving Mon 12 Jul 09:00.",
                      text)
        self.assertIn("To pay: 44.00 EUR (change fee 20.00 EUR, higher ticket price 24.00 EUR).", text)
        self.assertIn("Offer:          CE-MAL-0001-F (flex fare)", text)
        self.assertIn("Total price:    120.00 EUR (60.00 EUR per person)", text)
        self.assertIn("Seats left on this bus: 18", text)

    def test_a_free_change_that_costs_no_more_says_nothing_to_pay(self):
        reference = self.book("CE-MAL-0001-F")                      # flex, no fee, 60.00 EUR
        code, text = self.run_main("change", "--ref", reference, "--offer", "CE-MAL-0001-S")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Nothing to pay: the change is free and the new ticket does not cost more.", text)
        self.assertNotIn("To pay:", text)

    def test_a_ticket_that_cannot_be_changed_is_not_called_an_error(self):
        reference = self.book("SR-MAL-0001-B")
        code, text = self.run_main("change", "--ref", reference, "--offer", "SR-MAL-0001-S")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Not changed: this basic ticket cannot be changed.", text)
        self.assertNotIn("Error:", text)

    def test_a_full_new_bus_is_not_changed_and_not_called_booked(self):
        reference = self.book("CE-MAL-0001-S")
        code, text = self.run_main("change", "--ref", reference, "--offer", "CE-MAL-0002-S")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Not changed: the new bus has 0 seats left, but your booking is for 1", text)
        self.assertNotIn("Not booked", text)

    def test_a_full_bus_when_booking_is_still_called_not_booked(self):
        code, text = self.run_main("book", "--offer", "CE-MAL-0002-S")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Not booked: this bus is sold out", text)

    def test_the_change_is_logged_and_shown_in_the_list(self):
        reference = self.book("CE-MAL-0001-S")
        self.run_main("change", "--ref", reference, "--offer", "CE-MAL-0001-F")
        self.assertIn(f"Changed {reference}: offer CE-MAL-0001-S to CE-MAL-0001-F, fee 10.00 EUR, "
                      "price difference 12.00 EUR", self.read_log())
        _code, text = self.run_main("bookings")
        self.assertRegex(text, rf"{reference}\s+CE-MAL-0001-F")
        self.assertIn("To change or cancel a confirmed booking", text)

    def test_changing_does_not_need_the_profiles_file(self):
        reference = self.book("CE-MAL-0001-S")
        (self.data_dir / "profiles.json").write_text("{broken", encoding="utf-8")
        code, _text = self.run_main("change", "--ref", reference, "--offer", "CE-MAL-0001-F")
        self.assertEqual(code, EXIT_OK)

    def test_change_needs_a_reference_and_an_offer(self):
        from tests.test_boundaries import RequiredOptionTests
        reference = self.book("CE-MAL-0001-S")
        for arguments, missing in ((("--ref", reference), "--offer"), (("--offer", "CE-MAL-0001-F"), "--ref")):
            with self.subTest(missing=missing):
                code, text = RequiredOptionTests.run_usage_error(self, "change", *arguments)
                self.assertEqual(code, 2)
                self.assertIn(f"the following arguments are required: {missing}", text)


if __name__ == "__main__":
    unittest.main()
