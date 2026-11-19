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
import time
from collections import Counter
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from busbidder.exceptions import (
    BookingError,
    BookingNotFoundError,
    DataFileError,
    InvalidRecordError,
    NoSeatsError,
    NotChangeableError,
)
from busbidder.models import (
    PAYMENT_NOT_REFUNDED,
    PAYMENT_REFUNDED,
    STATUS_CANCELLED,
    Booking,
    Departure,
    Fare,
    count_of,
)
from busbidder.validation import is_booking_reference

logger = logging.getLogger("busbidder.booking")

BOOKINGS_FILE = "bookings.json"

# The same letters and digits the reference pattern allows: no I, O, 0 or 1.
REFERENCE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
REFERENCE_CODE_LENGTH = 6
MAX_REFERENCE_ATTEMPTS = 100

# Two copies of the program must not change the bookings file at the same
# moment. Whoever changes it first creates a lock file; the others wait.
LOCK_WAIT_SECONDS = 10.0
LOCK_RETRY_SECONDS = 0.02
# A lock this old was left behind by a run that was stopped, not by one
# that is still working: reading and writing the file takes milliseconds.
LOCK_STALE_SECONDS = 30.0


class BookingStore:
    """Reads and writes the bookings file."""

    def __init__(self, path: Path, wait_seconds: float = LOCK_WAIT_SECONDS) -> None:
        """Remember where the bookings file is and how long to wait for its lock."""
        self.path = path
        self.wait_seconds = wait_seconds

    @property
    def lock_path(self) -> Path:
        """The lock file that marks the bookings file as being changed."""
        return self.path.with_name(self.path.name + ".lock")

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the bookings file for one read, change and write.

        Creating a file that must not exist yet is one step for the
        operating system, on Windows and on Linux, so only one copy of the
        program can hold the lock. The others try again until it is free.
        Without the lock, two copies read the same bookings and the second
        to write wipes out the booking of the first.
        """
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                handle = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                break
            except (FileExistsError, PermissionError):
                # PermissionError: on Windows, the lock is being removed right now.
                if self._lock_is_stale():
                    self._remove_lock()
                    continue
                if time.monotonic() >= deadline:
                    raise DataFileError(
                        f"the bookings file {self.path} is in use by another copy of the "
                        "program, so nothing was changed. Try again in a moment"
                    ) from None
                time.sleep(LOCK_RETRY_SECONDS)
            except OSError as error:
                raise DataFileError(f"cannot lock bookings file {self.path}: {error}") from error
        os.close(handle)
        try:
            yield
        finally:
            self._remove_lock()

    def _lock_is_stale(self) -> bool:
        """Return True when the lock is too old to belong to a run that is still working."""
        try:
            return time.time() - self.lock_path.stat().st_mtime > LOCK_STALE_SECONDS
        except OSError:
            return False        # the lock has just gone, so try again

    def _remove_lock(self) -> None:
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError as error:
            # Nothing more can be done here. The lock goes stale and the
            # next run removes it, and the log says what happened.
            logger.warning("Could not remove the lock %s: %s", self.lock_path, error)

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
        # The process number keeps two copies of the program from sharing one temporary file.
        temporary = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
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


@dataclass(frozen=True)
class Change:
    """What a change of booking did and what it costs."""

    booking: Booking
    old_offer_id: str
    fee: float
    price_difference: float

    @property
    def to_pay(self) -> float:
        """The fee plus the higher price of the new ticket."""
        return round(self.fee + self.price_difference, 2)


def random_code() -> str:
    """Return six random characters for a booking reference."""
    return "".join(secrets.choice(REFERENCE_ALPHABET) for _ in range(REFERENCE_CODE_LENGTH))


class BookingService:
    """Makes, lists, changes and cancels simulated bookings.

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
            raise BookingError(
                f"unknown offer: {offer_id!r}, so nothing was booked. "
                "Use an offer ID from the search results, such as CL-MAL-0007-S."
            )
        departure = self._departures[fare.departure_id]
        # Reading, checking and writing happen under the lock, so no other copy
        # of the program can book the same seats in between.
        with self._store.locked():
            bookings = self._store.load()
            # Seats are counted again here, because another booking may have
            # been saved since the traveller saw the ranking.
            left = SeatLedger(self._departures, bookings).seats_left(departure.departure_id)
            bus = (
                f"{departure.company.name} bus to {departure.destination.name}, "
                f"{departure.departure:%a %d %b %H:%M}"
            )
            if left == 0:
                raise NoSeatsError(
                    f"this bus is sold out ({bus}). "
                    "Search again to see buses that still have seats."
                )
            if left < passengers:
                raise NoSeatsError(
                    f"this bus has only {count_of(left, 'seat')} left, but you asked for "
                    f"{passengers} ({bus}). Try fewer passengers or another bus."
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
        wanted = self._wanted(reference)
        with self._store.locked():
            bookings = self._store.load()
            booking = self._find(bookings, wanted)
            if not booking.is_active:
                raise BookingError(
                    f"booking {wanted} is already cancelled. "
                    f"Its refund was {booking.refund_amount or 0:.2f} EUR."
                )
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

    def change(self, reference: str, offer_id: str) -> Change:
        """Move a booking to another bus or fare of the same company and destination.

        Whether the ticket can be changed, and the fee, follow the fare
        conditions stored with the booking. The traveller pays the fee per
        passenger and the difference when the new ticket costs more. A
        cheaper ticket gives no money back. The reference stays the same.
        """
        wanted = self._wanted(reference)
        new_fare = self._fares.get(offer_id.strip().upper())
        if new_fare is None:
            raise BookingError(
                f"unknown offer: {offer_id!r}, so nothing was changed. "
                "Use an offer ID from the search results, such as CL-MAL-0007-S."
            )
        new_departure = self._departures[new_fare.departure_id]
        with self._store.locked():
            bookings = self._store.load()
            booking = self._find(bookings, wanted)
            if not booking.is_active:
                raise BookingError(f"booking {wanted} is cancelled, so it cannot be changed.")
            old_fare = booking.fare
            if not old_fare.changeable:
                raise NotChangeableError(
                    f"this {old_fare.fare_type} ticket cannot be changed. "
                    "You can cancel it with the cancel command and book another bus."
                )
            if new_fare.offer_id == old_fare.offer_id:
                raise BookingError(f"booking {wanted} is already for offer {old_fare.offer_id}.")
            if (new_departure.company.name != booking.company_name
                    or new_departure.destination.name != booking.destination_name):
                raise NotChangeableError(
                    f"a ticket can only be changed to another {booking.company_name} bus to "
                    f"{booking.destination_name}. Offer {new_fare.offer_id} is "
                    f"{new_departure.company.name} to {new_departure.destination.name}."
                )
            # The seats this booking holds are free for its own change.
            others = [saved for saved in bookings if saved is not booking]
            left = SeatLedger(self._departures, others).seats_left(new_departure.departure_id)
            if left < booking.passengers:
                raise NoSeatsError(
                    f"the new bus has {count_of(left, 'seat')} left, but your booking is for "
                    f"{booking.passengers} ({new_departure.departure:%a %d %b %H:%M}). "
                    "Your booking is unchanged."
                )
            new_total = round(new_fare.price * booking.passengers, 2)
            result = Change(
                booking=booking,
                old_offer_id=old_fare.offer_id,
                fee=round(old_fare.change_fee * booking.passengers, 2),
                price_difference=max(0.0, round(new_total - booking.total_price, 2)),
            )
            booking.fare = new_fare
            booking.total_price = new_total
            booking.departure = new_departure.departure
            booking.arrival = new_departure.arrival
            self._store.save(bookings)
        logger.info(
            "Changed %s: offer %s to %s, fee %.2f EUR, price difference %.2f EUR",
            wanted, result.old_offer_id, new_fare.offer_id, result.fee, result.price_difference,
        )
        return result

    @staticmethod
    def _wanted(reference: str) -> str:
        """Return the reference in its saved form, or say that it has the wrong form."""
        wanted = reference.strip().upper()
        if not is_booking_reference(wanted):
            raise BookingError(f"booking reference must look like BB-20261120-7K3QX9: {reference!r}")
        return wanted

    @staticmethod
    def _find(bookings: Iterable[Booking], wanted: str) -> Booking:
        """Return the saved booking with this reference."""
        booking = next((saved for saved in bookings if saved.reference == wanted), None)
        if booking is None:
            raise BookingNotFoundError(
                f"no booking found with the reference {wanted}. "
                "The bookings command lists your references."
            )
        return booking

    def _new_reference(self, moment: datetime, taken: set[str]) -> str:
        """Return a reference that no saved booking uses."""
        for _attempt in range(MAX_REFERENCE_ATTEMPTS):
            reference = f"BB-{moment:%Y%m%d}-{self._make_code()}"
            if reference not in taken:
                return reference
        raise BookingError("could not create a unique booking reference")
