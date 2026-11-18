"""Domain classes: what the marketplace is made of.

Each class checks its own values when it is created, so an object that
exists is always a valid one.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from numbers import Real
from typing import Any

from busbidder.exceptions import InvalidRecordError, InvalidRequestError
from busbidder.validation import (
    COMPANY_CODE_PATTERN,
    DATETIME_FORMAT,
    DESTINATION_CODE_PATTERN,
    FARE_TYPES,
    RATING_MAX,
    RATING_MIN,
    is_booking_reference,
    parse_clock_time,
    split_departure_id,
    split_offer_id,
)

MINUTES_PER_DAY = 24 * 60
MAX_DURATION_MINUTES = 24 * 60
MAX_REFUND_PERCENT = 100.0
# No bus fare in this marketplace comes near this. A higher value is far
# more likely a typing error (3819 for 38.19) than a real price, and one
# such value would flatten the price scores of every other offer.
MAX_PLAUSIBLE_PRICE_EUR = 500.0

STATUS_CONFIRMED = "confirmed"
STATUS_CANCELLED = "cancelled"
PAYMENT_SIMULATED = "paid (simulated)"
PAYMENT_REFUNDED = "refunded (simulated)"
PAYMENT_NOT_REFUNDED = "not refunded (the fare is not refundable)"


def count_of(number: int, word: str) -> str:
    """Return a number with its noun, such as '1 seat' or '2 seats'."""
    return f"{number} {word}" if number == 1 else f"{number} {word}s"


def format_duration(minutes: int) -> str:
    """Return minutes as text such as 6h 05m."""
    return f"{minutes // 60}h {minutes % 60:02d}m"


@dataclass(frozen=True)
class Company:
    """A bus company."""

    code: str
    name: str
    rating: float

    def __post_init__(self) -> None:
        if not COMPANY_CODE_PATTERN.match(self.code):
            raise InvalidRecordError(f"company code must be two capital letters: {self.code!r}")
        if not self.name.strip():
            raise InvalidRecordError("company name is missing")
        if not RATING_MIN <= self.rating <= RATING_MAX:
            raise InvalidRecordError(
                f"rating is outside {RATING_MIN:g} to {RATING_MAX:g}: {self.rating:g}"
            )


@dataclass(frozen=True)
class Destination:
    """A city served from Madrid."""

    code: str
    name: str
    distance_km: float

    def __post_init__(self) -> None:
        if not DESTINATION_CODE_PATTERN.match(self.code):
            raise InvalidRecordError(f"destination code must be three capital letters: {self.code!r}")
        if not self.name.strip():
            raise InvalidRecordError("destination name is missing")
        if self.distance_km <= 0:
            raise InvalidRecordError(f"distance must be above 0: {self.distance_km:g}")


@dataclass(frozen=True)
class Departure:
    """One bus run on one date. It owns the seats shared by all its fares."""

    departure_id: str
    company: Company
    destination: Destination
    departure: datetime
    arrival: datetime
    transfers: int
    seats: int
    emissions_kg: float | None = None

    def __post_init__(self) -> None:
        company_code, destination_code = split_departure_id(self.departure_id)
        if company_code != self.company.code:
            raise InvalidRecordError(
                f"departure ID {self.departure_id} does not match company {self.company.code}"
            )
        if destination_code != self.destination.code:
            raise InvalidRecordError(
                f"departure ID {self.departure_id} does not match destination {self.destination.code}"
            )
        if self.arrival <= self.departure:
            raise InvalidRecordError(
                f"arrival {self.arrival:{DATETIME_FORMAT}} is not after "
                f"departure {self.departure:{DATETIME_FORMAT}}"
            )
        if self.duration_minutes > MAX_DURATION_MINUTES:
            raise InvalidRecordError(f"journey is longer than 24 hours: {self.duration_minutes} minutes")
        if self.transfers < 0:
            raise InvalidRecordError(f"transfers is negative: {self.transfers}")
        if self.seats < 0:
            raise InvalidRecordError(f"seats is negative: {self.seats}")
        if self.emissions_kg is not None and self.emissions_kg < 0:
            raise InvalidRecordError(f"emissions is negative: {self.emissions_kg:g}")

    @property
    def duration_minutes(self) -> int:
        """Journey time in minutes, calculated from the two date-times."""
        return int((self.arrival - self.departure).total_seconds() // 60)


@dataclass(frozen=True)
class Fare:
    """One ticket type on a departure, with its price and conditions."""

    offer_id: str
    departure_id: str
    fare_type: str
    price: float
    changeable: bool
    refundable: bool
    change_fee: float
    refund_percent: float
    luggage_kg: int

    def __post_init__(self) -> None:
        departure_id, fare_letter = split_offer_id(self.offer_id)
        if departure_id != self.departure_id:
            raise InvalidRecordError(
                f"offer ID {self.offer_id} does not match departure {self.departure_id}"
            )
        if self.fare_type not in FARE_TYPES.values():
            raise InvalidRecordError(f"unknown fare type: {self.fare_type!r}")
        if FARE_TYPES[fare_letter] != self.fare_type:
            raise InvalidRecordError(
                f"offer ID {self.offer_id} does not match fare type {self.fare_type}"
            )
        if self.price <= 0:
            raise InvalidRecordError(f"price must be above 0: {self.price:g}")
        if self.price > MAX_PLAUSIBLE_PRICE_EUR:
            raise InvalidRecordError(
                f"price is implausibly high (above {MAX_PLAUSIBLE_PRICE_EUR:g} EUR): {self.price:g}"
            )
        if self.change_fee < 0:
            raise InvalidRecordError(f"change fee is negative: {self.change_fee:g}")
        if not self.changeable and self.change_fee > 0:
            raise InvalidRecordError(
                f"changeable is no but the change fee is {self.change_fee:g}"
            )
        if not 0 <= self.refund_percent <= MAX_REFUND_PERCENT:
            raise InvalidRecordError(f"refund percent is outside 0 to 100: {self.refund_percent:g}")
        if self.refundable != (self.refund_percent > 0):
            raise InvalidRecordError(
                f"refundable is {'yes' if self.refundable else 'no'} "
                f"but refund percent is {self.refund_percent:g}"
            )
        if self.luggage_kg < 0:
            raise InvalidRecordError(f"luggage is negative: {self.luggage_kg}")

    def to_dict(self) -> dict[str, Any]:
        """Return the fare as plain values that can be saved as JSON."""
        return {
            "offer_id": self.offer_id,
            "departure_id": self.departure_id,
            "fare_type": self.fare_type,
            "price": self.price,
            "changeable": self.changeable,
            "refundable": self.refundable,
            "change_fee": self.change_fee,
            "refund_percent": self.refund_percent,
            "luggage_kg": self.luggage_kg,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Fare:
        """Rebuild a fare from saved values."""
        return cls(**data)


@dataclass(frozen=True)
class Offer:
    """What a traveller can buy: one fare on one departure."""

    departure: Departure
    fare: Fare

    def __post_init__(self) -> None:
        if self.fare.departure_id != self.departure.departure_id:
            raise InvalidRecordError(
                f"fare {self.fare.offer_id} does not belong to departure {self.departure.departure_id}"
            )

    @property
    def offer_id(self) -> str:
        """The offer's identifier."""
        return self.fare.offer_id

    @property
    def price(self) -> float:
        """Price per person in euros."""
        return self.fare.price

    @property
    def company(self) -> Company:
        """The company that runs the bus."""
        return self.departure.company

    @property
    def duration_minutes(self) -> int:
        """Journey time in minutes."""
        return self.departure.duration_minutes


@dataclass(frozen=True)
class TimeBand:
    """A preferred period of the day, such as morning (06:00 to 12:00)."""

    name: str
    start: time
    end: time

    def minutes_outside(self, clock: time) -> int:
        """Return how many minutes a clock time lies outside the band.

        The result is 0 inside the band. Outside, it is the distance to the
        nearest edge, measured around the clock so 23:00 is one hour from
        midnight.
        """
        moment = _minute_of_day(clock)
        start = _minute_of_day(self.start)
        end = _minute_of_day(self.end)
        if start <= end:
            inside = start <= moment <= end
        else:
            inside = moment >= start or moment <= end
        if inside:
            return 0
        return min(_clock_distance(moment, start), _clock_distance(moment, end))

    def describe(self) -> str:
        """Return the band as text for the user."""
        if self.start == self.end:
            return f"{self.start:%H:%M}"
        return f"{self.name} ({self.start:%H:%M} to {self.end:%H:%M})"

    @classmethod
    def from_text(cls, text: str) -> TimeBand:
        """Build a band from a word (morning) or a clock time (08:30)."""
        key = (text or "").strip().lower()
        if key in TIME_BANDS:
            return TIME_BANDS[key]
        try:
            clock = parse_clock_time(key, "preferred departure")
        except InvalidRecordError:
            words = ", ".join(TIME_BANDS)
            raise InvalidRequestError(
                f"preferred departure must be one of {words}, or a time like 08:30: {text!r}"
            ) from None
        return cls(name=f"{clock:%H:%M}", start=clock, end=clock)


def _is_whole_number(value: object) -> bool:
    """Return True for an int that is not a bool."""
    return isinstance(value, int) and not isinstance(value, bool)


def _is_amount(value: object) -> bool:
    """Return True for a finite number of at least 0 that is not a bool."""
    return (
        isinstance(value, Real)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    )


def _minute_of_day(clock: time) -> int:
    """Return a clock time as minutes after midnight."""
    return clock.hour * 60 + clock.minute


def _clock_distance(first: int, second: int) -> int:
    """Return the shortest distance in minutes between two times of day."""
    difference = abs(first - second)
    return min(difference, MINUTES_PER_DAY - difference)


TIME_BANDS = {
    "morning": TimeBand("morning", time(6, 0), time(12, 0)),
    "afternoon": TimeBand("afternoon", time(12, 0), time(18, 0)),
    "evening": TimeBand("evening", time(18, 0), time(22, 0)),
    "night": TimeBand("night", time(22, 0), time(6, 0)),
}


@dataclass(frozen=True)
class TravelRequest:
    """What one traveller asks for: the trip and the mandatory conditions."""

    destination: Destination
    date_from: date
    date_to: date
    preferred_departure: TimeBand
    passengers: int = 1
    budget: float | None = None
    max_transfers: int | None = None
    max_duration_minutes: int | None = None
    require_changeable: bool = False
    require_refundable: bool = False
    min_luggage_kg: int | None = None

    def __post_init__(self) -> None:
        if self.date_to < self.date_from:
            raise InvalidRequestError(
                f"last travel date {self.date_to} is before first travel date {self.date_from}"
            )
        if self.passengers < 1:
            raise InvalidRequestError(f"passengers must be at least 1: {self.passengers}")
        if self.budget is not None and not (math.isfinite(self.budget) and self.budget > 0):
            raise InvalidRequestError(f"budget must be a number above 0: {self.budget:g}")
        for label, value in (
            ("maximum transfers", self.max_transfers),
            ("minimum luggage", self.min_luggage_kg),
        ):
            if value is not None and value < 0:
                raise InvalidRequestError(f"{label} is negative: {value}")
        if self.max_duration_minutes is not None and self.max_duration_minutes < 1:
            raise InvalidRequestError(
                f"longest journey must be at least 1 minute: {self.max_duration_minutes}"
            )


@dataclass(frozen=True)
class TravellerProfile:
    """A type of traveller: how much each criterion counts, in percent."""

    key: str
    description: str
    weights: dict[str, float] = field(default_factory=dict)


@dataclass
class Booking:
    """A simulated booking. It keeps its own copy of the fare conditions."""

    reference: str
    passengers: int
    total_price: float
    fare: Fare
    company_name: str
    destination_name: str
    departure: datetime
    arrival: datetime
    booked_at: datetime
    status: str = STATUS_CONFIRMED
    payment_status: str = PAYMENT_SIMULATED
    refund_amount: float | None = None

    def __post_init__(self) -> None:
        # A booking can come from the bookings file, which a person may have
        # edited, so every value is checked here as well.
        if not isinstance(self.reference, str) or not is_booking_reference(self.reference):
            raise InvalidRecordError(f"booking reference has the wrong form: {self.reference!r}")
        if not _is_whole_number(self.passengers) or self.passengers < 1:
            raise InvalidRecordError(f"passengers must be a whole number of at least 1: {self.passengers!r}")
        if not _is_amount(self.total_price):
            raise InvalidRecordError(f"total price must be a number of at least 0: {self.total_price!r}")
        if self.refund_amount is not None and not _is_amount(self.refund_amount):
            raise InvalidRecordError(f"refund must be a number of at least 0: {self.refund_amount!r}")
        if self.status not in (STATUS_CONFIRMED, STATUS_CANCELLED):
            raise InvalidRecordError(f"unknown booking status: {self.status!r}")
        if not isinstance(self.fare, Fare):
            raise InvalidRecordError("booking has no fare")

    @property
    def offer_id(self) -> str:
        """The offer that was booked."""
        return self.fare.offer_id

    @property
    def departure_id(self) -> str:
        """The departure whose seats this booking uses."""
        return self.fare.departure_id

    @property
    def is_active(self) -> bool:
        """True while the booking holds seats."""
        return self.status == STATUS_CONFIRMED

    def to_dict(self) -> dict[str, Any]:
        """Return the booking as plain values that can be saved as JSON."""
        return {
            "reference": self.reference,
            "passengers": self.passengers,
            "total_price": self.total_price,
            "fare": self.fare.to_dict(),
            "company_name": self.company_name,
            "destination_name": self.destination_name,
            "departure": self.departure.strftime(DATETIME_FORMAT),
            "arrival": self.arrival.strftime(DATETIME_FORMAT),
            "booked_at": self.booked_at.strftime(DATETIME_FORMAT),
            "status": self.status,
            "payment_status": self.payment_status,
            "refund_amount": self.refund_amount,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Booking:
        """Rebuild a booking from saved values."""
        values = dict(data)
        values["fare"] = Fare.from_dict(values["fare"])
        for name in ("departure", "arrival", "booked_at"):
            values[name] = datetime.strptime(values[name], DATETIME_FORMAT)
        return cls(**values)
