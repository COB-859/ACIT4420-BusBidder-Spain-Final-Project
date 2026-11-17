"""Saved bookings and the seat ledger.

The data files are never rewritten. Seats left on a departure are the
seats in the data file minus the seats held by active bookings, so the
count is always derived from one place: the saved bookings.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime
from pathlib import Path

from busbidder.exceptions import (
    BookingError,
    BookingNotFoundError,
    DataFileError,
    InvalidRecordError,
    NoSeatsError,
)
from busbidder.models import (
    PAYMENT_NOT_REFUNDED,
    PAYMENT_REFUNDED,
    STATUS_CANCELLED,
    Booking,
    Departure,
    Fare,
)
from busbidder.validation import is_booking_reference

logger = logging.getLogger("busbidder.booking")

BOOKINGS_FILE = "bookings.json"

# The same letters and digits the reference pattern allows: no I, O, 0 or 1.
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_CODE_LENGTH = 6
MAX_REFERENCE_ATTEMPTS = 100


class BookingStore:
    """Reads and writes the bookings file."""

    def __init__(self, path: Path) -> None:
        """Remember where the bookings file is."""
        self.path = path

    def load(self) -> list[Booking]:
        """Return the saved bookings. No file yet means no bookings."""
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except (OSError, UnicodeDecodeError) as error:
            raise DataFileError(f"cannot read bookings file {self.path}: {error}") from error
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            raise DataFileError(f"cannot read bookings file {self.path}: {error}") from error
        if not isinstance(data, list):
            raise DataFileError(f"bookings file {self.path} must hold a list of bookings")
        bookings: list[Booking] = []
        references: set[str] = set()
        for number, entry in enumerate(data, start=1):
            booking = self._booking_from(entry, number)
            if booking.reference in references:
                raise DataFileError(
                    f"bookings file {self.path}: booking {number} repeats reference {booking.reference}"
                )
            references.add(booking.reference)
            bookings.append(booking)
        return bookings

    def _booking_from(self, entry: object, number: int) -> Booking:
        """Build one booking from a saved entry, or say which entry is wrong."""
        try:
            return Booking.from_dict(entry)
        except InvalidRecordError as error:
            raise DataFileError(
                f"bookings file {self.path}: booking {number}: {error.reason}"
            ) from error
        except (TypeError, KeyError, ValueError, AttributeError) as error:
            # A hand-edited entry can have a missing key or a value of the
            # wrong type; all of these mean the entry is not a booking.
            raise DataFileError(
                f"bookings file {self.path}: booking {number} is not a valid booking record "
                f"({type(error).__name__}: {error})"
            ) from error

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
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                # The temporary file could not be removed either; the error
                # reported below is the one the user needs to see.
                pass
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


def random_code() -> str:
    """Return six random characters for a booking reference."""
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_CODE_LENGTH))


class BookingService:
    """Makes, lists and cancels simulated bookings.

    The clock and the code generator can be replaced, so tests can fix the
    date and force two bookings to draw the same code.
    """

    def __init__(
        self,
        store: BookingStore,
        departures: Mapping[str, Departure],
        fares: Mapping[str, Fare],
        now: Callable[[], datetime] = datetime.now,
        make_code: Callable[[], str] = random_code,
    ) -> None:
        """Remember where bookings are saved and what can be booked."""
        self._store = store
        self._departures = departures
        self._fares = fares
        self._now = now
        self._make_code = make_code

    def bookings(self) -> list[Booking]:
        """Return all saved bookings, cancelled ones included."""
        return self._store.load()

    def seats_left(self, departure_id: str) -> int:
        """Return the seats still free on a departure."""
        return SeatLedger(self._departures, self._store.load()).seats_left(departure_id)

    def book(self, offer_id: str, passengers: int) -> Booking:
        """Book an offer for a number of passengers and save the booking."""
        if passengers < 1:
            raise BookingError(f"passengers must be at least 1: {passengers}")
        fare = self._fares.get(offer_id.strip().upper())
        if fare is None:
            raise BookingError(f"unknown offer: {offer_id!r}")
        departure = self._departures[fare.departure_id]
        bookings = self._store.load()
        # Seats are counted again here, because another booking may have
        # been saved since the traveller saw the ranking.
        left = SeatLedger(self._departures, bookings).seats_left(departure.departure_id)
        if left < passengers:
            raise NoSeatsError(
                f"departure {departure.departure_id} has {left} seat(s) left, {passengers} requested"
            )
        moment = self._now()
        booking = Booking(
            reference=self._new_reference(moment, {saved.reference for saved in bookings}),
            passengers=passengers,
            total_price=round(fare.price * passengers, 2),
            fare=fare,
            company_name=departure.company.name,
            destination_name=departure.destination.name,
            departure=departure.departure,
            arrival=departure.arrival,
            booked_at=moment.replace(second=0, microsecond=0),
        )
        self._store.save([*bookings, booking])
        logger.info(
            "Booked %s: offer %s, %d passenger(s), %.2f EUR, %d seat(s) left on %s",
            booking.reference, fare.offer_id, passengers, booking.total_price,
            left - passengers, departure.departure_id,
        )
        return booking

    def cancel(self, reference: str) -> Booking:
        """Cancel a booking, free its seats and work out the simulated refund.

        The booking is kept with the status cancelled, so its reference is
        never given out again.
        """
        wanted = reference.strip().upper()
        if not is_booking_reference(wanted):
            raise BookingError(f"booking reference must look like BB-20261120-7K3QX9: {reference!r}")
        bookings = self._store.load()
        booking = next((saved for saved in bookings if saved.reference == wanted), None)
        if booking is None:
            raise BookingNotFoundError(f"no booking with reference {wanted}")
        if not booking.is_active:
            raise BookingError(f"booking {wanted} is already cancelled")
        # The refund follows the fare conditions stored with the booking,
        # not the data files, which may have changed since.
        refund = round(booking.total_price * booking.fare.refund_percent / 100, 2)
        booking.status = STATUS_CANCELLED
        booking.refund_amount = refund
        booking.payment_status = PAYMENT_REFUNDED if refund > 0 else PAYMENT_NOT_REFUNDED
        self._store.save(bookings)
        logger.info(
            "Cancelled %s: %d seat(s) freed on %s, refund %.2f EUR",
            wanted, booking.passengers, booking.departure_id, refund,
        )
        return booking

    def _new_reference(self, moment: datetime, taken: set[str]) -> str:
        """Return a reference that no saved booking uses."""
        for _attempt in range(MAX_REFERENCE_ATTEMPTS):
            reference = f"BB-{moment:%Y%m%d}-{self._make_code()}"
            if reference not in taken:
                return reference
        raise BookingError("could not create a unique booking reference")
