"""Tests for the saved bookings and the seat ledger."""

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from busbidder.booking import BookingStore, SeatLedger
from busbidder.exceptions import DataFileError
from busbidder.models import STATUS_CANCELLED, Booking
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


if __name__ == "__main__":
    unittest.main()
