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
        ):
            with self.subTest(error_class=error_class.__name__):
                self.assertTrue(issubclass(error_class, BusBidderError))

    def test_booking_errors_share_booking_error(self):
        self.assertTrue(issubclass(NoSeatsError, BookingError))
        self.assertTrue(issubclass(BookingNotFoundError, BookingError))

    def test_identifier_error_is_a_record_error(self):
        self.assertTrue(issubclass(InvalidIdentifierError, InvalidRecordError))


class InvalidRecordErrorTests(unittest.TestCase):
    """The message says where the bad row is."""

    def test_message_with_file_and_line(self):
        error = InvalidRecordError("price is negative", source="fares.csv", line=7)
        self.assertEqual(str(error), "fares.csv, line 7: price is negative")
        self.assertEqual(error.reason, "price is negative")
        self.assertEqual(error.line, 7)

    def test_message_with_file_only(self):
        error = InvalidRecordError("price is negative", source="fares.csv")
        self.assertEqual(str(error), "fares.csv: price is negative")

    def test_message_without_location(self):
        self.assertEqual(str(InvalidRecordError("price is negative")), "price is negative")

    def test_line_zero_is_still_shown(self):
        error = InvalidRecordError("empty file", source="fares.csv", line=0)
        self.assertEqual(str(error), "fares.csv, line 0: empty file")


if __name__ == "__main__":
    unittest.main()
