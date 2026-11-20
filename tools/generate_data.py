"""Generate the BusBidder Spain data files.

    python3 tools/generate_data.py [--out FOLDER]

Run it from the project folder. Without --out it writes to data.

The random generator has a fixed seed, so every run writes exactly the
same files. The numbers are invented; the companies are fictional.
"""

from __future__ import annotations

import argparse
import csv
import random
from datetime import date, datetime, time, timedelta
from pathlib import Path

SEED = 4420
FIRST_DAY = date(2027, 7, 10)
NUMBER_OF_DAYS = 7
DATETIME_FORMAT = "%Y-%m-%dT%H:%M"

# code, name, customer rating (1 to 5)
COMPANIES = [
    ("SR", "SolRuta", 3.6),
    ("CE", "Camino Express", 4.5),
    ("CL", "CostaLink", 4.0),
    ("VB", "VerdeBus", 4.4),
    ("NV", "NocheVia", 3.8),
]

# code, name, road distance from Madrid in km (rounded, for the simulation)
DESTINATIONS = [
    ("BAR", "Barcelona", 620),
    ("VAL", "Valencia", 355),
    ("SEV", "Seville", 530),
    ("MAL", "Malaga", 530),
    ("GRA", "Granada", 420),
    ("COR", "Cordoba", 395),
    ("BIL", "Bilbao", 400),
    ("SSE", "San Sebastian", 450),
    ("ZAR", "Zaragoza", 315),
    ("ALI", "Alicante", 420),
    ("MUR", "Murcia", 400),
    ("SAL", "Salamanca", 215),
    ("TOL", "Toledo", 75),
    ("SCO", "Santiago de Compostela", 600),
    ("CAD", "Cadiz", 650),
]
COASTAL = {"BAR", "VAL", "MAL", "SSE", "ALI", "CAD", "BIL"}

# How each company differs. Hours are the first and the last hour in which a
# bus leaves (the last departure can be at :45).
PROFILES = {
    "SR": {  # low-cost daytime services
        "price": 0.75, "speed": 1.15, "hours": (7, 17), "transfers": (0, 1, 1),
        "fares": ("basic", "standard"), "emissions": 0.030, "missing": 0.10,
    },
    "CE": {  # fast direct connections
        "price": 1.30, "speed": 0.90, "hours": (6, 20), "transfers": (0,),
        "fares": ("standard", "flex"), "emissions": 0.028, "missing": 0.0,
    },
    "CL": {  # strong coverage of coastal destinations
        "price": 1.00, "speed": 1.00, "hours": (8, 18), "transfers": (0, 0, 1),
        "fares": ("basic", "standard", "flex"), "emissions": 0.029, "missing": 0.05,
    },
    "VB": {  # environment-focused services
        "price": 1.15, "speed": 1.00, "hours": (7, 18), "transfers": (0, 1),
        "fares": ("standard", "flex"), "emissions": 0.017, "missing": 0.0,
    },
    "NV": {  # overnight and late-evening services
        "price": 0.85, "speed": 1.05, "hours": (20, 23), "transfers": (0,),
        "fares": ("basic", "standard"), "emissions": 0.031, "missing": 0.40,
    },
}

# letter, price multiplier, changeable, refundable, change fee, refund percent, luggage kg
FARE_RULES = {
    "basic": ("B", 1.0, False, False, 0, 0, 10),
    "standard": ("S", 1.2, True, False, 10, 0, 20),
    "flex": ("F", 1.5, True, True, 0, 90, 30),
}
# Company-specific fare conditions: (company, fare type) -> changed values.
FARE_OVERRIDES = {
    ("VB", "standard"): {"refundable": True, "refund_percent": 50},
    ("NV", "standard"): {"change_fee": 15},
}

BASE_PRICE = 4.0
PRICE_PER_KM = 0.055
BUS_SPEED_KMH = 80
MINUTES_PER_TRANSFER = 35
SHORT_ROUTE_KM = 200
SOLD_OUT_CHANCE = 0.06
SR_STANDARD_CHANCE = 0.5
QUARTER_HOUR = 15


def departures_per_day(company: str, destination: str, rng: random.Random) -> int:
    """Return how many buses a company runs to a destination on one day."""
    if company == "SR":
        return rng.choice((1, 2))
    if company == "CE":
        return 2
    if company == "CL":
        return 2 if destination in COASTAL else 1
    return 1


def pick_times(count: int, hours: tuple[int, int], rng: random.Random) -> list[time]:
    """Return distinct departure times on the quarter hour, earliest first."""
    first, last = hours
    slots = range(first * 60, last * 60 + 46, QUARTER_HOUR)
    minutes = sorted(rng.sample(slots, count))
    return [time(minute // 60, minute % 60) for minute in minutes]


def journey_minutes(distance_km: int, speed: float, transfers: int, rng: random.Random) -> int:
    """Return the journey time, rounded to five minutes."""
    driving = distance_km / BUS_SPEED_KMH * 60 * speed * rng.uniform(0.95, 1.05)
    total = driving + transfers * MINUTES_PER_TRANSFER
    return max(45, round(total / 5) * 5)


def yes_no(value: bool) -> str:
    """Return a true or false value as the text used in the files."""
    return "yes" if value else "no"


def build_rows(rng: random.Random) -> tuple[list[list], list[list]]:
    """Return the departure rows and the fare rows."""
    departures: list[list] = []
    fares: list[list] = []
    serials: dict[tuple[str, str], int] = {}
    for day_number in range(NUMBER_OF_DAYS):
        day = FIRST_DAY + timedelta(days=day_number)
        for company, _name, _rating in COMPANIES:
            profile = PROFILES[company]
            for destination, _city, distance in DESTINATIONS:
                count = departures_per_day(company, destination, rng)
                for clock in pick_times(count, profile["hours"], rng):
                    serial = serials.get((company, destination), 0) + 1
                    serials[(company, destination)] = serial
                    departure_id = f"{company}-{destination}-{serial:04d}"
                    transfers = 0 if distance < SHORT_ROUTE_KM else rng.choice(profile["transfers"])
                    leaves = datetime.combine(day, clock)
                    arrives = leaves + timedelta(
                        minutes=journey_minutes(distance, profile["speed"], transfers, rng)
                    )
                    seats = 0 if rng.random() < SOLD_OUT_CHANCE else rng.randint(2, 52)
                    emissions = round(distance * profile["emissions"] * rng.uniform(0.95, 1.05), 1)
                    reported = rng.random() >= profile["missing"]
                    departures.append([
                        departure_id, company, destination,
                        leaves.strftime(DATETIME_FORMAT), arrives.strftime(DATETIME_FORMAT),
                        transfers, seats, f"{emissions:.1f}" if reported else "",
                    ])
                    base_price = (
                        (BASE_PRICE + PRICE_PER_KM * distance) * profile["price"] * rng.uniform(0.92, 1.08)
                    )
                    for fare_type in profile["fares"]:
                        if company == "SR" and fare_type == "standard" and rng.random() >= SR_STANDARD_CHANCE:
                            continue
                        fares.append(fare_row(company, departure_id, fare_type, base_price))
    return departures, fares


def fare_row(company: str, departure_id: str, fare_type: str, base_price: float) -> list:
    """Return one row of the fares file."""
    letter, multiplier, changeable, refundable, change_fee, refund_percent, luggage = FARE_RULES[fare_type]
    values = {
        "changeable": changeable, "refundable": refundable,
        "change_fee": change_fee, "refund_percent": refund_percent,
    }
    values.update(FARE_OVERRIDES.get((company, fare_type), {}))
    return [
        f"{departure_id}-{letter}", departure_id, fare_type, f"{base_price * multiplier:.2f}",
        yes_no(values["changeable"]), yes_no(values["refundable"]),
        values["change_fee"], values["refund_percent"], luggage,
    ]


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    """Write one CSV file with Unix line endings, so the bytes never vary."""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    """Write the four main data files."""
    parser = argparse.ArgumentParser(description="Generate the BusBidder Spain data files.")
    parser.add_argument("--out", type=Path, default=Path("data"), help="output folder (default: data)")
    out_dir = parser.parse_args().out
    out_dir.mkdir(parents=True, exist_ok=True)
    departures, fares = build_rows(random.Random(SEED))
    write_csv(out_dir / "companies.csv", ["company_code", "name", "rating"], COMPANIES)
    write_csv(out_dir / "destinations.csv", ["destination_code", "name", "distance_km"], DESTINATIONS)
    write_csv(
        out_dir / "departures.csv",
        ["departure_id", "company_code", "destination_code", "departure", "arrival",
         "transfers", "seats", "emissions_kg"],
        departures,
    )
    write_csv(
        out_dir / "fares.csv",
        ["offer_id", "departure_id", "fare_type", "price_eur", "changeable", "refundable",
         "change_fee_eur", "refund_percent", "luggage_kg"],
        fares,
    )
    print(f"Wrote {len(COMPANIES)} companies, {len(DESTINATIONS)} destinations, "
          f"{len(departures)} departures and {len(fares)} fares to {out_dir}/")


if __name__ == "__main__":
    main()
