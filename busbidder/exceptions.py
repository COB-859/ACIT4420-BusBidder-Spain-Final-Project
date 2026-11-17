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

    def __init__(self, reason: str, source: str = "", line: int | None = None) -> None:
        """Keep the reason, the file name and the line number of the bad row."""
        self.reason = reason
        self.source = source
        self.line = line
        super().__init__(self._describe())

    def _describe(self) -> str:
        """Return the message with the file and line in front, when known."""
        if self.source and self.line is not None:
            return f"{self.source}, line {self.line}: {self.reason}"
        if self.source:
            return f"{self.source}: {self.reason}"
        return self.reason


class InvalidIdentifierError(InvalidRecordError):
    """A departure ID, offer ID or booking reference has the wrong format."""


class InvalidRequestError(BusBidderError):
    """The traveller's request cannot be used (unknown city, bad date)."""


class WeightError(BusBidderError):
    """The weights are negative, incomplete or do not total 100%."""


class BookingError(BusBidderError):
    """A booking could not be made, found or cancelled."""


class NoSeatsError(BookingError):
    """The departure does not have enough seats left for the party."""


class BookingNotFoundError(BookingError):
    """No saved booking has the given reference."""
