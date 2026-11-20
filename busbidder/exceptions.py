"""Custom exceptions for BusBidder Spain.

Every error that is meant to end a command is a BusBidderError, so main()
can catch that one class. The command-line module also raises ValueError
(guided-mode answers) and argparse.ArgumentTypeError (options) and handles
them itself.
"""

from __future__ import annotations


class BusBidderError(Exception):
    """Base class for every error that is meant to end a command."""


class DataFileError(BusBidderError):
    """A data, profiles, bookings or result file cannot be read, holds the wrong content, is in use or cannot be written."""


class InvalidRecordError(BusBidderError):
    """A value from a data file, the bookings file or the traveller is missing or impossible."""

    def __init__(self, reason: str) -> None:
        """Keep the reason, so the loader can record it with the file and line."""
        self.reason = reason
        super().__init__(reason)


class InvalidIdentifierError(InvalidRecordError):
    """A departure ID or an offer ID has the wrong format."""


class InvalidRequestError(BusBidderError):
    """The traveller's request cannot be used (unknown city, bad date)."""


class WeightError(BusBidderError):
    """The weights cannot be read, name an unknown criterion, are negative or incomplete, do not total 100 (or 1.0), or let one criterion dominate."""


class BookingError(BusBidderError):
    """A booking could not be made, found, changed or cancelled."""


class NoSeatsError(BookingError):
    """The departure does not have enough seats left for the party."""


class NotChangeableError(BookingError):
    """The ticket cannot be changed: its fare conditions do not allow it, or the new offer is of another company or destination."""


class BookingNotFoundError(BookingError):
    """No saved booking has the given reference."""
