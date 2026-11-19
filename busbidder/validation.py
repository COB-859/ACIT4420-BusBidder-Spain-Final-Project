"""Regular expressions and parsers that turn text into checked values.

Nothing here reads a file. The loader and the command-line module pass in
text, and get back a value of the right type or an InvalidRecordError.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime, time

from busbidder.exceptions import InvalidIdentifierError, InvalidRecordError

# Identifiers have a fixed structure, so a regular expression is the
# natural check. Named groups let the caller read the parts back.
COMPANY_CODE_PATTERN = re.compile(r"^[A-Z]{2}$")
DESTINATION_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")
DEPARTURE_ID_PATTERN = re.compile(r"^(?P<company>[A-Z]{2})-(?P<destination>[A-Z]{3})-(?P<serial>\d{4})$")
OFFER_ID_PATTERN = re.compile(r"^(?P<departure>[A-Z]{2}-[A-Z]{3}-\d{4})-(?P<fare>[BSF])$")
# The reference leaves out I, O, 0 and 1 because they are easy to misread.
BOOKING_REFERENCE_PATTERN = re.compile(r"^BB-(?P<date>\d{8})-(?P<code>[A-HJ-NP-Z2-9]{6})$")
CLOCK_TIME_PATTERN = re.compile(r"^(?P<hour>[01]?\d|2[0-3]):(?P<minute>[0-5]\d)$", re.ASCII)
DATETIME_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$", re.ASCII)
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$", re.ASCII)
# int() and float() also accept forms such as 3_0, 1e5, nan and digits from
# other scripts. A data file should hold plain numbers only.
INTEGER_PATTERN = re.compile(r"^[+-]?\d+$", re.ASCII)
DECIMAL_PATTERN = re.compile(r"^[+-]?(\d+(\.\d*)?|\.\d+)$", re.ASCII)

DATETIME_FORMAT = "%Y-%m-%dT%H:%M"
DATE_FORMAT = "%Y-%m-%d"

FARE_TYPES = {"B": "basic", "S": "standard", "F": "flex"}
RATING_MIN = 1.0
RATING_MAX = 5.0
YES_NO = {"yes": True, "no": False}


def require_text(value: str | None, field: str) -> str:
    """Return the value without surrounding spaces, or raise if it is empty."""
    text = (value or "").strip()
    if not text:
        raise InvalidRecordError(f"{field} is missing")
    return text


def parse_int(value: str | None, field: str, minimum: int | None = None) -> int:
    """Return the value as a whole number, checked against a minimum."""
    text = require_text(value, field)
    if not INTEGER_PATTERN.match(text):
        raise InvalidRecordError(f"{field} is not a whole number: {text!r}")
    number = int(text)
    if minimum is not None and number < minimum:
        raise InvalidRecordError(f"{field} is below {minimum}: {number}")
    return number


def parse_float(
    value: str | None,
    field: str,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    """Return the value as a number, checked against a minimum and a maximum."""
    text = require_text(value, field)
    if not DECIMAL_PATTERN.match(text):
        raise InvalidRecordError(f"{field} is not a number: {text!r}")
    number = float(text)
    # A very long run of digits still overflows to infinity.
    if not math.isfinite(number):
        raise InvalidRecordError(f"{field} is not a usable number: {text!r}")
    if minimum is not None and number < minimum:
        raise InvalidRecordError(f"{field} is below {minimum:g}: {number:g}")
    if maximum is not None and number > maximum:
        raise InvalidRecordError(f"{field} is above {maximum:g}: {number:g}")
    return number


def parse_optional_float(value: str | None, field: str, minimum: float | None = None) -> float | None:
    """Return None for an empty value, otherwise the checked number."""
    if not (value or "").strip():
        return None
    return parse_float(value, field, minimum=minimum)


def parse_yes_no(value: str | None, field: str) -> bool:
    """Return True for 'yes' and False for 'no'."""
    text = require_text(value, field).lower()
    if text not in YES_NO:
        raise InvalidRecordError(f"{field} must be yes or no: {text!r}")
    return YES_NO[text]


def parse_datetime(value: str | None, field: str) -> datetime:
    """Return a date and time written as 2027-07-12T08:30."""
    text = require_text(value, field)
    if not DATETIME_PATTERN.match(text):
        raise InvalidRecordError(f"{field} must look like 2027-07-12T08:30: {text!r}")
    try:
        return datetime.strptime(text, DATETIME_FORMAT)
    except ValueError:
        raise InvalidRecordError(f"{field} is not a real date and time: {text!r}") from None


def parse_date(value: str | None, field: str) -> date:
    """Return a date written as 2027-07-12."""
    text = require_text(value, field)
    if not DATE_PATTERN.match(text):
        raise InvalidRecordError(f"{field} must look like 2027-07-12: {text!r}")
    try:
        return datetime.strptime(text, DATE_FORMAT).date()
    except ValueError:
        raise InvalidRecordError(f"{field} is not a real date: {text!r}") from None


def parse_clock_time(value: str | None, field: str = "time") -> time:
    """Return a clock time written as 08:30."""
    text = require_text(value, field)
    match = CLOCK_TIME_PATTERN.match(text)
    if not match:
        raise InvalidRecordError(f"{field} must look like 08:30: {text!r}")
    return time(int(match["hour"]), int(match["minute"]))


def split_departure_id(value: str | None) -> tuple[str, str]:
    """Return the company code and destination code inside a departure ID."""
    text = (value or "").strip()
    match = DEPARTURE_ID_PATTERN.match(text)
    if not match:
        raise InvalidIdentifierError(f"departure ID must look like SR-MAL-0001: {text!r}")
    return match["company"], match["destination"]


def split_offer_id(value: str | None) -> tuple[str, str]:
    """Return the departure ID and the fare letter inside an offer ID."""
    text = (value or "").strip()
    match = OFFER_ID_PATTERN.match(text)
    if not match:
        raise InvalidIdentifierError(f"offer ID must look like SR-MAL-0001-B: {text!r}")
    return match["departure"], match["fare"]


def is_booking_reference(value: str | None) -> bool:
    """Return True when the text has the form of a booking reference."""
    return bool(BOOKING_REFERENCE_PATTERN.match((value or "").strip()))
