"""Tests for the custom exceptions."""

import unittest

from busbidder.exceptions import (
    BookingError,
    BookingNotFoundError,
    BusBidderError,
    DataFileError,
    InvalidIdentifierError,
    InvalidRecordError,
    InvalidRequestError,
    NoSeatsError,
    NotChangeableError,
    WeightError,
)


class ExceptionHierarchyTests(unittest.TestCase):
    """Every custom error can be caught as a BusBidderError."""

    def test_all_errors_share_the_base_class(self):
        for error_class in (
            DataFileError,
            InvalidRecordError,
            InvalidIdentifierError,
            InvalidRequestError,
            WeightError,
            BookingError,
            NoSeatsError,
            BookingNotFoundError,
            NotChangeableError,
        ):
            with self.subTest(error_class=error_class.__name__):
                self.assertTrue(issubclass(error_class, BusBidderError))

    def test_booking_errors_share_booking_error(self):
        self.assertTrue(issubclass(NoSeatsError, BookingError))
        self.assertTrue(issubclass(BookingNotFoundError, BookingError))
        self.assertTrue(issubclass(NotChangeableError, BookingError))

    def test_identifier_error_is_a_record_error(self):
        self.assertTrue(issubclass(InvalidIdentifierError, InvalidRecordError))


class InvalidRecordErrorTests(unittest.TestCase):
    """The reason is kept apart, so the loader can record it with file and line."""

    def test_reason_is_kept_and_is_the_message(self):
        error = InvalidRecordError("price is negative")
        self.assertEqual(error.reason, "price is negative")
        self.assertEqual(str(error), "price is negative")

    def test_identifier_error_keeps_its_reason_too(self):
        self.assertEqual(InvalidIdentifierError("bad ID").reason, "bad ID")


if __name__ == "__main__":
    unittest.main()
