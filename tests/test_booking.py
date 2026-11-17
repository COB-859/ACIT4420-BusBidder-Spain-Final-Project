"""Tests for the saved bookings and the seat ledger."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from busbidder.booking import BookingService, BookingStore, SeatLedger, random_code
from busbidder.exceptions import BookingError, DataFileError, NoSeatsError
from busbidder.models import STATUS_CANCELLED, Booking
from busbidder.validation import is_booking_reference
from tests.helpers import make_departure, make_fare


def make_booking(reference: str = "BB-20261120-AAAAAA", departure_id: str = "SR-MAL-0001",
                 letter: str = "B", passengers: int = 1, status: str = "confirmed") -> Booking:
    """Return a booking on the given departure."""
    fare = make_fare(departure_id, letter)
    return Booking(
        reference=reference,
        passengers=passengers,
        total_price=fare.price * passengers,
        fare=fare,
        company_name="SolRuta",
        destination_name="Malaga",
        departure=datetime(2027, 7, 12, 8, 0),
        arrival=datetime(2027, 7, 12, 14, 0),
        booked_at=datetime(2026, 11, 20, 9, 0),
        status=status,
    )


class BookingStoreTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "state" / "bookings.json"
        self.store = BookingStore(self.path)

    def test_no_file_means_no_bookings(self):
        self.assertEqual(self.store.load(), [])

    def test_bookings_are_saved_and_read_back(self):
        bookings = [make_booking(), make_booking("BB-20261120-BBBBBB", letter="S", passengers=3)]
        self.store.save(bookings)
        self.assertEqual(self.store.load(), bookings)

    def test_saving_creates_the_folder_and_leaves_no_temporary_file(self):
        self.store.save([make_booking()])
        self.assertEqual([path.name for path in self.path.parent.iterdir()], ["bookings.json"])

    def test_saving_an_empty_list_is_allowed(self):
        self.store.save([])
        self.assertEqual(self.store.load(), [])

    def test_broken_file_is_a_data_file_error(self):
        self.path.parent.mkdir(parents=True)
        for text in ("{not json", '[{"reference": "BB-1"}]', '{"a": 1}'):
            self.path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(DataFileError):
                self.store.load()


class SeatLedgerTests(unittest.TestCase):
    def setUp(self):
        self.departures = {
            "SR-MAL-0001": make_departure(serial=1, seats=5),
            "SR-MAL-0002": make_departure(serial=2, seats=0),
        }

    def test_without_bookings_all_seats_are_left(self):
        ledger = SeatLedger(self.departures, [])
        self.assertEqual(ledger.seats_left("SR-MAL-0001"), 5)
        self.assertEqual(ledger.all_seats_left(), {"SR-MAL-0001": 5, "SR-MAL-0002": 0})

    def test_bookings_on_any_fare_use_the_same_seats(self):
        bookings = [
            make_booking("BB-20261120-AAAAAA", letter="B", passengers=2),
            make_booking("BB-20261120-BBBBBB", letter="F", passengers=1),
        ]
        self.assertEqual(SeatLedger(self.departures, bookings).seats_left("SR-MAL-0001"), 2)

    def test_cancelled_bookings_hold_no_seats(self):
        bookings = [make_booking(passengers=2, status=STATUS_CANCELLED)]
        self.assertEqual(SeatLedger(self.departures, bookings).seats_left("SR-MAL-0001"), 5)

    def test_last_seat_boundary(self):
        bookings = [make_booking(passengers=5)]
        self.assertEqual(SeatLedger(self.departures, bookings).seats_left("SR-MAL-0001"), 0)

    def test_more_bookings_than_seats_gives_zero_and_a_warning(self):
        bookings = [make_booking(passengers=9)]
        with self.assertLogs("busbidder.booking", level="WARNING") as logs:
            ledger = SeatLedger(self.departures, bookings)
        self.assertEqual(ledger.seats_left("SR-MAL-0001"), 0)
        self.assertIn("has 5 seats but 9 are booked", logs.output[0])

    def test_booking_for_unknown_departure_is_logged_and_ignored(self):
        bookings = [make_booking(departure_id="SR-MAL-0099")]
        with self.assertLogs("busbidder.booking", level="WARNING") as logs:
            ledger = SeatLedger(self.departures, bookings)
        self.assertIn("not in the data files", logs.output[0])
        self.assertEqual(ledger.seats_left("SR-MAL-0001"), 5)

    def test_unknown_departure_has_no_seats(self):
        self.assertEqual(SeatLedger(self.departures, []).seats_left("XX-XXX-0001"), 0)


class BookingServiceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.store = BookingStore(Path(temp.name) / "bookings.json")
        self.departures = {
            "SR-MAL-0001": make_departure(serial=1, seats=3),
            "SR-MAL-0002": make_departure(serial=2, seats=0),
        }
        self.fares = {
            "SR-MAL-0001-B": make_fare("SR-MAL-0001", "B", price=25.0),
            "SR-MAL-0001-F": make_fare("SR-MAL-0001", "F", price=45.5, changeable=True, refundable=True),
            "SR-MAL-0002-B": make_fare("SR-MAL-0002", "B", price=25.0),
        }
        self.codes = iter(["AAAAAA", "AAAAAA", "BBBBBB", "CCCCCC", "DDDDDD"])
        self.service = self.make_service()

    def make_service(self, make_code=None) -> BookingService:
        return BookingService(
            self.store, self.departures, self.fares,
            now=lambda: datetime(2026, 11, 20, 9, 15, 42),
            make_code=make_code or (lambda: next(self.codes)),
        )

    def test_booking_is_created_saved_and_confirmed(self):
        booking = self.service.book("SR-MAL-0001-F", 2)
        self.assertEqual(booking.reference, "BB-20261120-AAAAAA")
        self.assertEqual(booking.total_price, 91.0)
        self.assertEqual(booking.status, "confirmed")
        self.assertEqual(booking.payment_status, "paid (simulated)")
        self.assertEqual(booking.booked_at, datetime(2026, 11, 20, 9, 15))
        self.assertEqual(self.store.load(), [booking])

    def test_fare_conditions_are_stored_with_the_booking(self):
        self.service.book("SR-MAL-0001-F", 1)
        saved = self.store.load()[0]
        self.assertTrue(saved.fare.changeable)
        self.assertEqual(saved.fare.refund_percent, 80.0)
        self.assertEqual(saved.fare.luggage_kg, 20)

    def test_seats_drop_for_every_fare_on_the_same_departure(self):
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 3)
        self.service.book("SR-MAL-0001-F", 2)
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 1)
        with self.assertRaises(NoSeatsError):
            self.service.book("SR-MAL-0001-B", 2)

    def test_last_seat_can_be_booked_once_only(self):
        self.service.book("SR-MAL-0001-B", 3)
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 0)
        with self.assertRaises(NoSeatsError) as caught:
            self.service.book("SR-MAL-0001-B", 1)
        self.assertIn("has 0 seat(s) left, 1 requested", str(caught.exception))
        self.assertEqual(len(self.store.load()), 1)

    def test_sold_out_departure_cannot_be_booked(self):
        with self.assertRaises(NoSeatsError):
            self.service.book("SR-MAL-0002-B", 1)
        self.assertEqual(self.store.load(), [])

    def test_seats_are_counted_from_the_saved_file_not_from_memory(self):
        self.service.book("SR-MAL-0001-B", 2)
        second_service = self.make_service()
        with self.assertRaises(NoSeatsError):
            second_service.book("SR-MAL-0001-F", 2)

    def test_same_code_twice_gives_a_new_reference(self):
        first = self.service.book("SR-MAL-0001-B", 1)
        second = self.service.book("SR-MAL-0001-B", 1)
        self.assertEqual(first.reference, "BB-20261120-AAAAAA")
        self.assertEqual(second.reference, "BB-20261120-BBBBBB")

    def test_giving_up_when_no_unique_reference_can_be_made(self):
        service = self.make_service(make_code=lambda: "AAAAAA")
        service.book("SR-MAL-0001-B", 1)
        with self.assertRaises(BookingError) as caught:
            service.book("SR-MAL-0001-B", 1)
        self.assertIn("unique booking reference", str(caught.exception))

    def test_default_reference_matches_the_pattern(self):
        service = BookingService(self.store, self.departures, self.fares)
        self.assertTrue(is_booking_reference(service.book("SR-MAL-0001-B", 1).reference))
        for _ in range(50):
            self.assertRegex(random_code(), r"^[A-HJ-NP-Z2-9]{6}$")

    def test_unknown_offer_and_bad_passenger_count_are_rejected(self):
        with self.assertRaises(BookingError) as caught:
            self.service.book("SR-MAL-0099-B", 1)
        self.assertIn("unknown offer", str(caught.exception))
        for passengers in (0, -1):
            with self.subTest(passengers=passengers), self.assertRaises(BookingError):
                self.service.book("SR-MAL-0001-B", passengers)

    def test_offer_id_is_accepted_in_lower_case(self):
        self.assertEqual(self.service.book(" sr-mal-0001-b ", 1).offer_id, "SR-MAL-0001-B")

    def test_booking_is_recorded_in_the_log(self):
        with self.assertLogs("busbidder.booking", level="INFO") as logs:
            self.service.book("SR-MAL-0001-B", 1)
        self.assertIn("Booked BB-20261120-AAAAAA: offer SR-MAL-0001-B, 1 passenger(s), "
                      "25.00 EUR, 2 seat(s) left on SR-MAL-0001", logs.output[0])


if __name__ == "__main__":
    unittest.main()
