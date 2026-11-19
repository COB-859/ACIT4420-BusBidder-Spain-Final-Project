"""Custom exceptions for BusBidder Spain.

Every error the program raises on purpose is a BusBidderError, so the
command-line module can catch that one class and print a clear message.
"""

from __future__ import annotations


class BusBidderError(Exception):
    """Base class for every error raised by this program."""


class DataFileError(BusBidderError):
    """A data file is missing, unreadable or has the wrong columns."""


class InvalidRecordError(BusBidderError):
    """One row of a data file holds a missing or impossible value."""

    def __init__(self, reason: str) -> None:
        """Keep the reason, so the loader can record it with the file and line."""
        self.reason = reason
        super().__init__(reason)


class InvalidIdentifierError(InvalidRecordError):
    """A departure ID or an offer ID has the wrong format."""


class InvalidRequestError(BusBidderError):
    """The traveller's request cannot be used (unknown city, bad date)."""


class WeightError(BusBidderError):
    """The weights are negative, incomplete or do not total 100%."""


class BookingError(BusBidderError):
    """A booking could not be made, found or cancelled."""


class NoSeatsError(BookingError):
    """The departure does not have enough seats left for the party."""


class NotChangeableError(BookingError):
    """The ticket's fare conditions do not allow a change."""


class BookingNotFoundError(BookingError):
    """No saved booking has the given reference."""
