"""Tests for the saved bookings and the seat ledger."""

import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime
from unittest import mock
from pathlib import Path

from busbidder.booking import LOCK_STALE_SECONDS, BookingService, BookingStore, SeatLedger, random_code
from busbidder.exceptions import BookingError, BookingNotFoundError, DataFileError, NoSeatsError
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

    def test_a_failed_save_leaves_the_old_file_whole(self):
        # The new bookings are written to a temporary file and moved into
        # place in one step. When that step fails, the old file must be
        # untouched and no temporary file may be left behind.
        self.store.save([make_booking()])
        before = self.path.read_text(encoding="utf-8")
        with mock.patch("busbidder.booking.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(DataFileError) as caught:
                self.store.save([make_booking(), make_booking("BB-20261120-BBBBBB")])
        self.assertIn("cannot write bookings file", str(caught.exception))
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertEqual([entry.name for entry in self.path.parent.iterdir()], ["bookings.json"])
        self.assertEqual(len(self.store.load()), 1)

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
        for text in ("{not json", '[{"reference": "BB-1"}]', '{"a": 1}', "{}", "[1]", "[{}]"):
            self.path.write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(DataFileError):
                self.store.load()

    def test_a_file_that_cannot_be_decoded_is_a_data_file_error(self):
        # A number of 5,000 digits and very deep nesting stop the JSON reader itself.
        self.path.parent.mkdir(parents=True)
        for text in ('[{"passengers": ' + "1" * 5000 + "}]", "[" * 100000 + "]" * 100000):
            self.path.write_text(text, encoding="utf-8")
            with self.subTest(start=text[:18]):
                with self.assertRaises(DataFileError) as caught:
                    self.store.load()
                self.assertIn("cannot read bookings file", str(caught.exception))

    def test_an_entry_with_odd_field_names_gives_one_printable_line(self):
        # The name of an unknown field ends up in the message, so it must not break the line.
        for name in ("\ud800", "a\nb"):
            entry = make_booking().to_dict()
            entry["fare"][name] = 1
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([entry]), encoding="utf-8")
            with self.subTest(name=ascii(name)):
                with self.assertRaises(DataFileError) as caught:
                    self.store.load()
                message = str(caught.exception)
                self.assertIn("booking 1 is not a valid booking record", message)
                self.assertTrue(message.isprintable(), ascii(message))

    def save_edited(self, **changes):
        """Save one valid booking with some values replaced, as a person editing the file might."""
        entry = make_booking().to_dict()
        fare_changes = changes.pop("fare", {})
        entry.update(changes)
        entry["fare"].update(fare_changes)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps([entry]), encoding="utf-8")

    def test_hand_edited_values_are_rejected_with_the_booking_number(self):
        cases = (
            ({"passengers": "2"}, "passengers must be a whole number of at least 1"),
            ({"passengers": None}, "passengers must be a whole number of at least 1"),
            ({"passengers": -30}, "passengers must be a whole number of at least 1"),
            ({"passengers": True}, "passengers must be a whole number of at least 1"),
            ({"status": "Confirmed"}, "unknown booking status"),
            ({"status": "pending"}, "unknown booking status"),
            ({"total_price": "35.0"}, "total price must be a number"),
            ({"refund_amount": -1}, "refund must be a number"),
            ({"reference": None}, "booking reference has the wrong form"),
            ({"reference": "not-a-reference"}, "booking reference has the wrong form"),
            ({"fare": {"price": -5}}, "price must be above 0"),
            ({"fare": {"price": float("nan")}}, "price is not a number"),
            ({"fare": {"change_fee": float("inf")}}, "change fee is not a number"),
            ({"fare": {"luggage_kg": 20.5}}, "luggage is not a whole number"),
            ({"fare": {"changeable": "yes"}}, "changeable must be yes or no"),
            ({"total_price": float("nan")}, "total price must be a number"),
            ({"fare": {"offer_id": 5}}, "is not a valid booking record"),
            ({"departure": "yesterday"}, "is not a valid booking record"),
        )
        for changes, message in cases:
            with self.subTest(changes=changes):
                self.save_edited(**changes)
                with self.assertRaises(DataFileError) as caught:
                    self.store.load()
                self.assertIn("booking 1", str(caught.exception))
                self.assertIn(message, str(caught.exception))

    def test_a_record_without_a_defaulted_key_is_rejected(self):
        for key in ("status", "payment_status", "refund_amount"):
            entry = make_booking().to_dict()
            del entry[key]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps([entry]), encoding="utf-8")
            with self.subTest(key=key), self.assertRaises(DataFileError) as caught:
                self.store.load()
            self.assertIn("booking 1", str(caught.exception))
            self.assertIn(f"booking record lacks {key}", str(caught.exception))

    def test_a_number_too_large_for_a_float_is_rejected(self):
        self.save_edited(total_price=10**400)
        with self.assertRaises(DataFileError) as caught:
            self.store.load()
        self.assertIn("booking 1", str(caught.exception))
        self.assertIn("total price must be a number", str(caught.exception))

    def test_same_reference_twice_is_rejected(self):
        entry = make_booking().to_dict()
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps([entry, entry]), encoding="utf-8")
        with self.assertRaises(DataFileError) as caught:
            self.store.load()
        self.assertIn("booking 2 repeats reference BB-20261120-AAAAAA", str(caught.exception))

    def test_directory_in_place_of_the_file_is_an_error_not_an_empty_list(self):
        self.path.mkdir(parents=True)
        with self.assertRaises(DataFileError):
            self.store.load()

    def test_failed_save_leaves_no_temporary_file(self):
        self.path.mkdir(parents=True)   # a folder where the file should be: replacing it fails
        with self.assertRaises(DataFileError):
            self.store.save([make_booking()])
        self.assertEqual([p.name for p in self.path.parent.iterdir()], ["bookings.json"])


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


class ServiceTestCase(unittest.TestCase):
    """A booking service on a small data set, with a fixed clock and fixed codes."""

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


class LockTests(ServiceTestCase):
    """Two copies of the program must not change the bookings file at the same moment."""

    def test_the_lock_is_held_during_the_change_and_removed_after_it(self):
        self.assertFalse(self.store.lock_path.exists())
        with self.store.locked():
            self.assertTrue(self.store.lock_path.exists())
        self.assertFalse(self.store.lock_path.exists())

    def test_a_held_lock_stops_a_second_booking_with_a_clear_message(self):
        impatient = BookingStore(self.store.path, wait_seconds=0.1)
        service = BookingService(impatient, self.departures, self.fares)
        with self.store.locked():
            with self.assertRaises(DataFileError) as caught:
                service.book("SR-MAL-0001-B", 1)
            self.assertIn("is in use by another copy of the program, or its folder cannot be written, so nothing was changed",
                          str(caught.exception))
            # The run that holds the lock keeps it.
            self.assertTrue(self.store.lock_path.exists())
        self.assertEqual(self.store.load(), [])

    def test_a_second_booking_goes_through_once_the_lock_is_free(self):
        with self.store.locked():
            pass
        self.assertEqual(self.service.book("SR-MAL-0001-B", 1).passengers, 1)

    def test_a_lock_left_behind_by_a_stopped_run_is_removed(self):
        self.store.lock_path.write_text("", encoding="utf-8")
        long_ago = time.time() - LOCK_STALE_SECONDS - 5
        os.utime(self.store.lock_path, (long_ago, long_ago))
        self.service.book("SR-MAL-0001-B", 1)
        self.assertEqual(len(self.store.load()), 1)
        self.assertFalse(self.store.lock_path.exists())

    def test_a_fresh_lock_is_not_taken_for_a_stale_one(self):
        self.store.lock_path.write_text("", encoding="utf-8")
        impatient = BookingStore(self.store.path, wait_seconds=0.05)
        with self.assertRaises(DataFileError):
            with impatient.locked():
                self.fail("the lock of another run was taken")
        self.assertTrue(self.store.lock_path.exists())

    def test_the_lock_is_removed_when_the_booking_is_refused_or_the_save_fails(self):
        with self.assertRaises(NoSeatsError):
            self.service.book("SR-MAL-0002-B", 1)
        self.assertFalse(self.store.lock_path.exists())
        with mock.patch("busbidder.booking.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(DataFileError):
                self.service.book("SR-MAL-0001-B", 1)
        self.assertFalse(self.store.lock_path.exists())
        with self.assertRaises(BookingNotFoundError):
            self.service.cancel("BB-20261120-ZZZZZZ")
        self.assertFalse(self.store.lock_path.exists())

    def test_eight_bookings_at_once_for_three_seats_save_exactly_three(self):
        # Without the lock, several threads read "3 seats left" before any
        # of them writes, and bookings are lost or the bus is overbooked.
        outcomes = []

        def book_one_seat() -> None:
            service = BookingService(BookingStore(self.store.path), self.departures, self.fares)
            try:
                outcomes.append(service.book("SR-MAL-0001-B", 1).reference)
            except NoSeatsError:
                outcomes.append("sold out")

        threads = [threading.Thread(target=book_one_seat) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        confirmed = {outcome for outcome in outcomes if outcome != "sold out"}
        self.assertEqual(len(outcomes), 8)
        self.assertEqual(len(confirmed), 3)
        self.assertEqual({booking.reference for booking in self.store.load()}, confirmed)
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 0)

    def test_each_run_writes_through_its_own_temporary_file(self):
        with mock.patch("busbidder.booking.os.replace", side_effect=OSError("stop here")) as replace:
            with self.assertRaises(DataFileError):
                self.store.save([make_booking()])
        temporary = Path(replace.call_args.args[0])
        self.assertEqual(temporary.name, f"bookings.json.{os.getpid()}.tmp")


class BookingServiceTests(ServiceTestCase):

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
        self.assertIn("this bus is sold out (SolRuta bus to Malaga, Mon 12 Jul 08:00)", str(caught.exception))
        self.assertEqual(len(self.store.load()), 1)

    def test_sold_out_departure_cannot_be_booked(self):
        with self.assertRaises(NoSeatsError) as caught:
            self.service.book("SR-MAL-0002-B", 1)
        self.assertIn("is sold out", str(caught.exception))
        self.assertEqual(self.store.load(), [])

    def test_too_few_seats_says_how_many_are_left(self):
        with self.assertRaises(NoSeatsError) as caught:
            self.service.book("SR-MAL-0001-B", 4)
        message = str(caught.exception)
        self.assertIn("this bus has only 3 seats left, but you asked for 4", message)
        self.assertIn("(SolRuta bus to Malaga, Mon 12 Jul 08:00). Try fewer passengers or another bus.", message)
        self.assertNotIn("sold out", message)

    def test_seats_are_counted_from_the_saved_file_not_from_memory(self):
        self.service.book("SR-MAL-0001-B", 2)
        second_service = self.make_service()
        with self.assertRaises(NoSeatsError):
            second_service.book("SR-MAL-0001-F", 2)

    def test_a_second_booking_does_not_replace_the_first(self):
        first = self.service.book("SR-MAL-0001-B", 1)
        second = self.service.book("SR-MAL-0001-F", 1)
        self.assertEqual([saved.reference for saved in self.store.load()],
                         [first.reference, second.reference])
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 1)

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


class CancelTests(ServiceTestCase):

    def test_cancel_frees_the_seats_and_keeps_the_record(self):
        booking = self.service.book("SR-MAL-0001-F", 2)
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 1)
        cancelled = self.service.cancel(booking.reference)
        self.assertEqual(cancelled.status, "cancelled")
        self.assertEqual(self.service.seats_left("SR-MAL-0001"), 3)
        saved = self.store.load()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].status, "cancelled")

    def test_refund_follows_the_stored_fare_conditions(self):
        booking = self.service.book("SR-MAL-0001-F", 2)   # 91.00 EUR, 80% refundable
        cancelled = self.service.cancel(booking.reference)
        self.assertEqual(cancelled.refund_amount, 72.8)
        self.assertEqual(cancelled.payment_status, "refunded (simulated)")

    def test_refund_is_rounded_to_whole_cents(self):
        self.fares["SR-MAL-0001-F"] = make_fare("SR-MAL-0001", "F", price=33.33,
                                                changeable=True, refundable=True)
        booking = self.make_service().book("SR-MAL-0001-F", 1)   # 80% of 33.33 is 26.664
        self.assertEqual(self.service.cancel(booking.reference).refund_amount, 26.66)

    def test_non_refundable_fare_gets_no_refund(self):
        booking = self.service.book("SR-MAL-0001-B", 1)
        cancelled = self.service.cancel(booking.reference)
        self.assertEqual(cancelled.refund_amount, 0.0)
        self.assertEqual(cancelled.payment_status, "not refunded (the fare is not refundable)")

    def test_refund_uses_the_booking_copy_even_if_the_data_changes(self):
        booking = self.service.book("SR-MAL-0001-F", 1)
        self.fares["SR-MAL-0001-F"] = make_fare("SR-MAL-0001", "F", price=45.5, changeable=True)
        cancelled = self.make_service().cancel(booking.reference)
        self.assertEqual(cancelled.refund_amount, 36.4)

    def test_cancelling_twice_is_an_error(self):
        booking = self.service.book("SR-MAL-0001-B", 1)
        self.service.cancel(booking.reference)
        with self.assertRaises(BookingError) as caught:
            self.service.cancel(booking.reference)
        self.assertIn("is already cancelled", str(caught.exception))
        self.assertIn("Its refund was 0.00 EUR", str(caught.exception))

    def test_unknown_reference_is_not_found(self):
        with self.assertRaises(BookingNotFoundError):
            self.service.cancel("BB-20261120-ZZZZZZ")

    def test_malformed_reference_is_rejected(self):
        for reference in ("12345", "BB-2026-AAAAAA", ""):
            with self.subTest(reference=reference), self.assertRaises(BookingError) as caught:
                self.service.cancel(reference)
            self.assertIn("must look like", str(caught.exception))

    def test_reference_is_accepted_in_lower_case(self):
        booking = self.service.book("SR-MAL-0001-B", 1)
        self.assertEqual(self.service.cancel(booking.reference.lower()).status, "cancelled")

    def test_a_cancelled_reference_is_never_reused(self):
        first = self.service.book("SR-MAL-0001-B", 1)     # code AAAAAA
        self.service.cancel(first.reference)
        second = self.service.book("SR-MAL-0001-B", 1)    # draws AAAAAA again, then BBBBBB
        self.assertEqual(second.reference, "BB-20261120-BBBBBB")

    def test_cancellation_is_recorded_in_the_log(self):
        booking = self.service.book("SR-MAL-0001-F", 1)
        with self.assertLogs("busbidder.booking", level="INFO") as logs:
            self.service.cancel(booking.reference)
        self.assertIn("Cancelled BB-20261120-AAAAAA: 1 seat(s) freed on SR-MAL-0001, refund 36.40 EUR",
                      logs.output[0])


if __name__ == "__main__":
    unittest.main()
