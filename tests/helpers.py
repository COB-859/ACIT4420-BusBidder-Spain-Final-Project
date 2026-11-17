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


SMALL_DATA = {
    "companies.csv": (
        "company_code,name,rating\n"
        "SR,SolRuta,3.6\n"
        "CE,Camino Express,4.5\n"
    ),
    "destinations.csv": (
        "destination_code,name,distance_km\n"
        "MAL,Malaga,530\n"
        "TOL,Toledo,75\n"
    ),
    "departures.csv": (
        "departure_id,company_code,destination_code,departure,arrival,transfers,seats,emissions_kg\n"
        "SR-MAL-0001,SR,MAL,2027-07-12T08:00,2027-07-12T15:00,1,3,15.9\n"
        "CE-MAL-0001,CE,MAL,2027-07-12T09:00,2027-07-12T14:30,0,20,14.8\n"
        "CE-MAL-0002,CE,MAL,2027-07-13T19:00,2027-07-14T00:30,0,0,\n"
        "SR-TOL-0001,SR,TOL,2027-07-12T10:00,2027-07-12T11:00,0,12,2.3\n"
    ),
    "fares.csv": (
        "offer_id,departure_id,fare_type,price_eur,changeable,refundable,"
        "change_fee_eur,refund_percent,luggage_kg\n"
        "SR-MAL-0001-B,SR-MAL-0001,basic,25.00,no,no,0,0,10\n"
        "SR-MAL-0001-S,SR-MAL-0001,standard,30.00,yes,no,10,0,20\n"
        "CE-MAL-0001-S,CE-MAL-0001,standard,48.00,yes,no,10,0,20\n"
        "CE-MAL-0001-F,CE-MAL-0001,flex,60.00,yes,yes,0,90,30\n"
        "CE-MAL-0002-S,CE-MAL-0002,standard,45.00,yes,no,10,0,20\n"
        "SR-TOL-0001-B,SR-TOL-0001,basic,8.00,no,no,0,0,10\n"
    ),
    "profiles.json": (
        '{"budget": {"description": "Price first", '
        '"weights": {"price": 55, "duration": 15, "time": 10, "flex": 10, "transfers": 10}}, '
        '"flexible": {"description": "Flexibility first", '
        '"weights": {"price": 20, "duration": 15, "time": 15, "flex": 50}}}'
    ),
}


def write_data_dir(folder, **replacements: str):
    """Write a small valid data folder. A keyword such as fares_csv replaces one file."""
    folder.mkdir(parents=True, exist_ok=True)
    files = dict(SMALL_DATA)
    for name, text in replacements.items():
        stem, _, extension = name.rpartition("_")
        files[f"{stem}.{extension}"] = text
    for name, text in files.items():
        (folder / name).write_text(text, encoding="utf-8")
    return folder
