"""Tests for the parsers and regular expressions."""

import unittest
from datetime import date, datetime, time

from busbidder.exceptions import InvalidIdentifierError, InvalidRecordError
from busbidder.validation import (
    is_booking_reference,
    parse_clock_time,
    parse_date,
    parse_datetime,
    parse_float,
    parse_int,
    parse_optional_float,
    parse_yes_no,
    require_text,
    split_departure_id,
    split_offer_id,
)


class TextAndNumberTests(unittest.TestCase):
    def test_require_text_strips_spaces(self):
        self.assertEqual(require_text("  Malaga ", "name"), "Malaga")

    def test_require_text_rejects_empty_and_none(self):
        for value in ("", "   ", None):
            with self.subTest(value=value), self.assertRaises(InvalidRecordError) as caught:
                require_text(value, "name")
            self.assertEqual(caught.exception.reason, "name is missing")

    def test_parse_int_normal(self):
        self.assertEqual(parse_int(" 40 ", "seats", minimum=0), 40)

    def test_parse_int_boundary_at_minimum(self):
        self.assertEqual(parse_int("0", "seats", minimum=0), 0)

    def test_parse_int_rejects_below_minimum_text_and_decimals(self):
        for value in ("-1", "many", "3.5", ""):
            with self.subTest(value=value), self.assertRaises(InvalidRecordError):
                parse_int(value, "seats", minimum=0)

    def test_parse_int_with_too_many_digits_is_a_record_error(self):
        with self.assertRaises(InvalidRecordError) as caught:
            parse_int("9" * 5000, "seats")
        self.assertIn("too many digits", caught.exception.reason)

    def test_parse_float_normal(self):
        self.assertEqual(parse_float("29.90", "price", minimum=0), 29.9)

    def test_parse_float_boundaries_are_accepted(self):
        self.assertEqual(parse_float("1", "rating", minimum=1, maximum=5), 1.0)
        self.assertEqual(parse_float("5", "rating", minimum=1, maximum=5), 5.0)

    def test_parse_float_rejects_out_of_range(self):
        for value in ("0.9", "5.1"):
            with self.subTest(value=value), self.assertRaises(InvalidRecordError):
                parse_float(value, "rating", minimum=1, maximum=5)

    def test_parse_float_rejects_nan_and_infinity(self):
        for value in ("nan", "inf", "-inf"):
            with self.subTest(value=value), self.assertRaises(InvalidRecordError):
                parse_float(value, "price")

    def test_parse_optional_float_blank_is_none(self):
        self.assertIsNone(parse_optional_float("", "emissions"))
        self.assertIsNone(parse_optional_float(None, "emissions"))

    def test_parse_optional_float_value_is_checked(self):
        self.assertEqual(parse_optional_float("12.5", "emissions", minimum=0), 12.5)
        with self.assertRaises(InvalidRecordError):
            parse_optional_float("-2", "emissions", minimum=0)

    def test_parse_yes_no(self):
        self.assertTrue(parse_yes_no("Yes", "changeable"))
        self.assertFalse(parse_yes_no("no", "changeable"))
        with self.assertRaises(InvalidRecordError):
            parse_yes_no("maybe", "changeable")


class DateAndTimeTests(unittest.TestCase):
    def test_parse_datetime_normal(self):
        self.assertEqual(parse_datetime("2027-07-12T08:30", "departure"), datetime(2027, 7, 12, 8, 30))

    def test_parse_datetime_rejects_impossible_date(self):
        with self.assertRaises(InvalidRecordError) as caught:
            parse_datetime("2027-07-32T08:30", "departure")
        self.assertIn("not a real date", caught.exception.reason)

    def test_parse_datetime_rejects_other_formats(self):
        for value in ("12/07/2027 08:30", "2027-7-12T8:30", "2027-07-12", ""):
            with self.subTest(value=value), self.assertRaises(InvalidRecordError):
                parse_datetime(value, "departure")

    def test_parse_date(self):
        self.assertEqual(parse_date("2027-07-12", "date"), date(2027, 7, 12))
        for value in ("2027-02-30", "12 July", ""):
            with self.subTest(value=value), self.assertRaises(InvalidRecordError):
                parse_date(value, "date")

    def test_parse_clock_time_normal_and_boundaries(self):
        self.assertEqual(parse_clock_time("08:30"), time(8, 30))
        self.assertEqual(parse_clock_time("8:30"), time(8, 30))
        self.assertEqual(parse_clock_time("00:00"), time(0, 0))
        self.assertEqual(parse_clock_time("23:59"), time(23, 59))

    def test_parse_clock_time_rejects_impossible_times(self):
        for value in ("24:00", "12:60", "noon", "0830", ""):
            with self.subTest(value=value), self.assertRaises(InvalidRecordError):
                parse_clock_time(value)


class IdentifierTests(unittest.TestCase):
    def test_split_departure_id(self):
        self.assertEqual(split_departure_id("SR-MAL-0001"), ("SR", "MAL"))

    def test_split_departure_id_rejects_malformed(self):
        for value in ("SR_MAL_1", "sr-mal-0001", "SR-MAL-001", "SR-MALA-0001", "SR-MAL-0001-B", "", None):
            with self.subTest(value=value), self.assertRaises(InvalidIdentifierError):
                split_departure_id(value)

    def test_digits_from_other_scripts_are_not_accepted_in_a_departure_id(self):
        with self.assertRaises(InvalidIdentifierError):
            split_departure_id("SR-MAL-\u0660\u0660\u0660\u0661")   # Arabic-Indic digits

    def test_split_offer_id(self):
        self.assertEqual(split_offer_id("SR-MAL-0001-F"), ("SR-MAL-0001", "F"))

    def test_split_offer_id_rejects_malformed(self):
        for value in ("SR-MAL-0001", "SR-MAL-0001-X", "SR-MAL-0001-BB", "", None):
            with self.subTest(value=value), self.assertRaises(InvalidIdentifierError):
                split_offer_id(value)

    def test_identifier_error_is_also_a_record_error(self):
        with self.assertRaises(InvalidRecordError):
            split_offer_id("nonsense")

    def test_booking_reference(self):
        self.assertTrue(is_booking_reference("BB-20261120-7K3QX9"))

    def test_booking_reference_rejects_malformed(self):
        for value in ("BB-20261120-7K3QX", "BB-2026112-7K3QX9", "XX-20261120-7K3QX9",
                      "BB-20261120-7K3QO9", "BB-20261120-7K3Q19", "bb-20261120-7k3qx9", "", None):
            with self.subTest(value=value):
                self.assertFalse(is_booking_reference(value))

    def test_digits_from_other_scripts_are_not_accepted_in_a_booking_reference(self):
        self.assertFalse(is_booking_reference("BB-\u0662\u0660\u0662\u0666\u0661\u0661\u0662\u0660-7K3QX9"))


if __name__ == "__main__":
    unittest.main()
