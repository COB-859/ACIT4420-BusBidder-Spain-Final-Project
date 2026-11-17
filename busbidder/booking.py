"""Saved bookings and the seat ledger.

The data files are never rewritten. Seats left on a departure are the
seats in the data file minus the seats held by active bookings, so the
count is always derived from one place: the saved bookings.
"""

from __future__ import annotations

import json
import logging
import os
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path

from busbidder.exceptions import DataFileError
from busbidder.models import Booking, Departure

logger = logging.getLogger("busbidder.booking")

BOOKINGS_FILE = "bookings.json"


class BookingStore:
    """Reads and writes the bookings file."""

    def __init__(self, path: Path) -> None:
        """Remember where the bookings file is."""
        self.path = path

    def load(self) -> list[Booking]:
        """Return the saved bookings. No file yet means no bookings."""
        if not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [Booking.from_dict(entry) for entry in data]
        except (OSError, UnicodeDecodeError, ValueError, TypeError, KeyError) as error:
            raise DataFileError(f"cannot read bookings file {self.path}: {error}") from error

    def save(self, bookings: Iterable[Booking]) -> None:
        """Write all bookings. A crash cannot leave half a file behind."""
        text = json.dumps([booking.to_dict() for booking in bookings], indent=2)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(text + "\n", encoding="utf-8")
            # Replacing a file is one step for the operating system, so the
            # bookings file is always either the old version or the new one.
            os.replace(temporary, self.path)
        except OSError as error:
            raise DataFileError(f"cannot write bookings file {self.path}: {error}") from error


class SeatLedger:
    """Works out how many seats are left on each departure."""

    def __init__(self, departures: Mapping[str, Departure], bookings: Iterable[Booking]) -> None:
        """Count the seats held by active bookings, per departure."""
        self._departures = departures
        self._booked: Counter[str] = Counter()
        for booking in bookings:
            if not booking.is_active:
                continue
            if booking.departure_id not in departures:
                logger.warning(
                    "Booking %s is for departure %s, which is not in the data files",
                    booking.reference, booking.departure_id,
                )
                continue
            self._booked[booking.departure_id] += booking.passengers
        for departure_id, booked in self._booked.items():
            seats = departures[departure_id].seats
            if booked > seats:
                logger.warning(
                    "Departure %s has %d seats but %d are booked", departure_id, seats, booked
                )

    def seats_left(self, departure_id: str) -> int:
        """Return the seats still free on a departure, never below 0."""
        departure = self._departures.get(departure_id)
        if departure is None:
            return 0
        return max(0, departure.seats - self._booked[departure_id])

    def all_seats_left(self) -> dict[str, int]:
        """Return the seats left for every departure."""
        return {departure_id: self.seats_left(departure_id) for departure_id in self._departures}
