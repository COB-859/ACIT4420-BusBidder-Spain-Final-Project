"""Small builders for test objects, so each test states only what matters to it."""

from __future__ import annotations

from datetime import date, datetime

from busbidder.models import (
    Company,
    Departure,
    Destination,
    Fare,
    Offer,
    TimeBand,
    TravelRequest,
)
from busbidder.validation import FARE_TYPES

COMPANY = Company("SR", "SolRuta", 3.6)
DESTINATION = Destination("MAL", "Malaga", 530.0)


def make_departure(
    serial: int = 1,
    company: Company = COMPANY,
    destination: Destination = DESTINATION,
    departure: str = "2027-07-12T08:00",
    arrival: str = "2027-07-12T14:00",
    transfers: int = 0,
    seats: int = 40,
    emissions_kg: float | None = 15.0,
) -> Departure:
    """Return a departure with sensible defaults."""
    return Departure(
        departure_id=f"{company.code}-{destination.code}-{serial:04d}",
        company=company,
        destination=destination,
        departure=datetime.fromisoformat(departure),
        arrival=datetime.fromisoformat(arrival),
        transfers=transfers,
        seats=seats,
        emissions_kg=emissions_kg,
    )


def make_fare(
    departure_id: str = "SR-MAL-0001",
    letter: str = "B",
    price: float = 30.0,
    changeable: bool = False,
    refundable: bool = False,
    change_fee: float = 0.0,
    refund_percent: float | None = None,
    luggage_kg: int = 20,
) -> Fare:
    """Return a fare with sensible defaults."""
    if refund_percent is None:
        refund_percent = 80.0 if refundable else 0.0
    return Fare(
        offer_id=f"{departure_id}-{letter}",
        departure_id=departure_id,
        fare_type=FARE_TYPES[letter],
        price=price,
        changeable=changeable,
        refundable=refundable,
        change_fee=change_fee,
        refund_percent=refund_percent,
        luggage_kg=luggage_kg,
    )


def make_offer(letter: str = "B", **options) -> Offer:
    """Return an offer. Fare options and departure options can be mixed."""
    fare_names = {"price", "changeable", "refundable", "change_fee", "refund_percent", "luggage_kg"}
    fare_options = {name: value for name, value in options.items() if name in fare_names}
    departure_options = {name: value for name, value in options.items() if name not in fare_names}
    departure = make_departure(**departure_options)
    return Offer(departure, make_fare(departure.departure_id, letter, **fare_options))


def make_request(**options) -> TravelRequest:
    """Return a request for Malaga, 12 to 14 July 2027, morning."""
    values = {
        "destination": DESTINATION,
        "date_from": date(2027, 7, 12),
        "date_to": date(2027, 7, 14),
        "preferred_departure": TimeBand.from_text("morning"),
    }
    values.update(options)
    return TravelRequest(**values)
