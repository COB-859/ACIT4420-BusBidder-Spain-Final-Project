"""Reads the data files, builds the objects and sets bad rows aside.

Policy: a bad row is rejected and recorded, never corrected. Loading
continues with the next row. A missing file or a missing column stops the
load with a DataFileError, because nothing useful can be read from it.
"""

from __future__ import annotations

import csv
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from busbidder.exceptions import DataFileError, InvalidRecordError
from busbidder.models import Company, Departure, Destination, Fare, Offer, TravellerProfile
from busbidder.validation import (
    RATING_MAX,
    RATING_MIN,
    parse_datetime,
    parse_float,
    parse_int,
    parse_optional_float,
    parse_yes_no,
    require_text,
    split_departure_id,
    split_offer_id,
)

logger = logging.getLogger("busbidder.loader")

COMPANY_COLUMNS = ("company_code", "name", "rating")
DESTINATION_COLUMNS = ("destination_code", "name", "distance_km")
DEPARTURE_COLUMNS = (
    "departure_id", "company_code", "destination_code", "departure", "arrival",
    "transfers", "seats", "emissions_kg",
)
FARE_COLUMNS = (
    "offer_id", "departure_id", "fare_type", "price_eur", "changeable", "refundable",
    "change_fee_eur", "refund_percent", "luggage_kg",
)

# The main file is required. The file of hand-written bad rows is read
# after it when it exists, so the rejection path runs on every load.
COMPANY_FILES = ("companies.csv", "companies_invalid.csv")
DESTINATION_FILES = ("destinations.csv",)
DEPARTURE_FILES = ("departures.csv", "departures_invalid.csv")
FARE_FILES = ("fares.csv", "fares_invalid.csv")
PROFILES_FILE = "profiles.json"
REJECTED_FILE = "rejected_records.txt"


@dataclass(frozen=True)
class RejectedRecord:
    """One row that was set aside, with where it was and why."""

    source: str
    line: int
    reason: str
    row: str

    def describe(self) -> str:
        """Return the record as one line of text."""
        return f"{self.source}, line {self.line}: {self.reason}"


@dataclass
class LoadResult:
    """Everything that was read: the valid objects and the rejected rows."""

    companies: dict[str, Company] = field(default_factory=dict)
    destinations: dict[str, Destination] = field(default_factory=dict)
    departures: dict[str, Departure] = field(default_factory=dict)
    fares: dict[str, Fare] = field(default_factory=dict)
    rejected: list[RejectedRecord] = field(default_factory=list)

    def offers(self) -> list[Offer]:
        """Return every fare joined with its departure."""
        return [Offer(self.departures[fare.departure_id], fare) for fare in self.fares.values()]

    def travel_days(self) -> tuple[date, date] | None:
        """Return the first and the last day with a departure, or None without data."""
        days = [departure.departure.date() for departure in self.departures.values()]
        return (min(days), max(days)) if days else None

    def sold_out(self) -> list[Departure]:
        """Return the departures that have no seats in the data file."""
        return [departure for departure in self.departures.values() if departure.seats == 0]


def read_rows(path: Path, columns: tuple[str, ...]) -> list[tuple[int, dict[str, str]]]:
    """Return the rows of a CSV file as (line number, values) pairs."""
    try:
        # utf-8-sig also accepts a file saved by Excel with a byte-order mark.
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames
            if header is None:
                raise DataFileError(f"data file is empty: {path}")
            missing = [column for column in columns if column not in header]
            if missing:
                raise DataFileError(f"{path.name} has no column: {', '.join(missing)}")
            return [(reader.line_num, row) for row in reader]
    except FileNotFoundError:
        raise DataFileError(f"data file not found: {path}") from None
    except (OSError, UnicodeDecodeError, csv.Error) as error:
        raise DataFileError(f"cannot read {path}: {error}") from error


def build_company(row: dict[str, str]) -> Company:
    """Turn one row of the companies file into a Company."""
    return Company(
        code=require_text(row["company_code"], "company code"),
        name=require_text(row["name"], "company name"),
        rating=parse_float(row["rating"], "rating", minimum=RATING_MIN, maximum=RATING_MAX),
    )


def build_destination(row: dict[str, str]) -> Destination:
    """Turn one row of the destinations file into a Destination."""
    return Destination(
        code=require_text(row["destination_code"], "destination code"),
        name=require_text(row["name"], "destination name"),
        distance_km=parse_float(row["distance_km"], "distance"),
    )


def build_departure(
    row: dict[str, str],
    companies: dict[str, Company],
    destinations: dict[str, Destination],
) -> Departure:
    """Turn one row of the departures file into a Departure."""
    departure_id = require_text(row["departure_id"], "departure ID")
    split_departure_id(departure_id)
    company_code = require_text(row["company_code"], "company code")
    if company_code not in companies:
        raise InvalidRecordError(f"unknown company: {company_code!r}")
    destination_code = require_text(row["destination_code"], "destination code")
    if destination_code not in destinations:
        raise InvalidRecordError(f"unknown destination: {destination_code!r}")
    return Departure(
        departure_id=departure_id,
        company=companies[company_code],
        destination=destinations[destination_code],
        departure=parse_datetime(row["departure"], "departure"),
        arrival=parse_datetime(row["arrival"], "arrival"),
        transfers=parse_int(row["transfers"], "transfers", minimum=0),
        seats=parse_int(row["seats"], "seats", minimum=0),
        emissions_kg=parse_optional_float(row["emissions_kg"], "emissions", minimum=0),
    )


def build_fare(row: dict[str, str], departures: dict[str, Departure]) -> Fare:
    """Turn one row of the fares file into a Fare."""
    offer_id = require_text(row["offer_id"], "offer ID")
    split_offer_id(offer_id)
    departure_id = require_text(row["departure_id"], "departure ID")
    if departure_id not in departures:
        raise InvalidRecordError(f"unknown departure: {departure_id!r}")
    return Fare(
        offer_id=offer_id,
        departure_id=departure_id,
        fare_type=require_text(row["fare_type"], "fare type"),
        price=parse_float(row["price_eur"], "price"),
        changeable=parse_yes_no(row["changeable"], "changeable"),
        refundable=parse_yes_no(row["refundable"], "refundable"),
        change_fee=parse_float(row["change_fee_eur"], "change fee"),
        refund_percent=parse_float(row["refund_percent"], "refund percent"),
        luggage_kg=parse_int(row["luggage_kg"], "luggage"),
    )


def _load_into(
    store: dict[str, Any],
    data_dir: Path,
    file_names: tuple[str, ...],
    columns: tuple[str, ...],
    build: Callable[[dict[str, str]], Any],
    key_of: Callable[[Any], str],
    kind: str,
    rejected: list[RejectedRecord],
) -> None:
    """Read one kind of record from its files into store, setting bad rows aside."""
    for position, file_name in enumerate(file_names):
        path = data_dir / file_name
        if position > 0 and not path.exists():
            logger.info("Optional file %s not found, skipped", file_name)
            continue
        accepted = 0
        for line, row in read_rows(path, columns):
            try:
                _check_cell_count(row)
                item = build(row)
                key = key_of(item)
                if key in store:
                    raise InvalidRecordError(f"duplicate {kind}: {key}")
                store[key] = item
                accepted += 1
            except InvalidRecordError as error:
                raw = ",".join(row.get(column) or "" for column in columns)
                rejected.append(RejectedRecord(file_name, line, error.reason, raw))
                logger.warning("Rejected %s, line %d: %s", file_name, line, error.reason)
        if position == 0 and accepted == 0:
            # Without this, an emptied main file would let a row from the
            # file of invalid examples become the only "valid" record.
            raise DataFileError(f"{file_name} holds no valid rows")


def _check_cell_count(row: dict) -> None:
    """Reject a row with more or fewer values than the header has columns.

    csv.DictReader keeps extra values under the key None and fills missing
    ones with None, so a misplaced comma would otherwise go unnoticed.
    """
    extra = row.get(None)
    if extra:
        raise InvalidRecordError(f"row has {len(extra)} value(s) too many")
    missing = [column for column, value in row.items() if column is not None and value is None]
    if missing:
        raise InvalidRecordError(f"row has too few values: nothing for {', '.join(missing)}")


def load_all(data_dir: Path) -> LoadResult:
    """Read companies, destinations, departures and fares from data_dir."""
    result = LoadResult()
    _load_into(result.companies, data_dir, COMPANY_FILES, COMPANY_COLUMNS,
               build_company, lambda company: company.code, "company code", result.rejected)
    _load_into(result.destinations, data_dir, DESTINATION_FILES, DESTINATION_COLUMNS,
               build_destination, lambda destination: destination.code, "destination code",
               result.rejected)
    _load_into(result.departures, data_dir, DEPARTURE_FILES, DEPARTURE_COLUMNS,
               lambda row: build_departure(row, result.companies, result.destinations),
               lambda departure: departure.departure_id, "departure ID", result.rejected)
    _load_into(result.fares, data_dir, FARE_FILES, FARE_COLUMNS,
               lambda row: build_fare(row, result.departures),
               lambda fare: fare.offer_id, "offer ID", result.rejected)
    logger.info(
        "Loaded %d companies, %d destinations, %d departures, %d fares; rejected %d rows",
        len(result.companies), len(result.destinations), len(result.departures),
        len(result.fares), len(result.rejected),
    )
    sold_out = result.sold_out()
    if sold_out:
        # A full bus is a normal state, not a fault, so this is not a warning.
        logger.info(
            "%d departures are sold out in the data files (0 seats): %s",
            len(sold_out), ", ".join(departure.departure_id for departure in sold_out),
        )
    return result


def write_rejected(rejected: list[RejectedRecord], path: Path) -> None:
    """Write the rejected rows to a text file, replacing the earlier one."""
    lines = [
        "BusBidder Spain: rows set aside while loading the data files",
        f"Rejected rows: {len(rejected)}",
        "",
    ]
    for record in rejected:
        lines.append(record.describe())
        lines.append(f"    row: {record.row}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as error:
        raise DataFileError(f"cannot write {path}: {error}") from error


def load_profiles(path: Path) -> dict[str, TravellerProfile]:
    """Read the traveller profiles from a JSON file."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DataFileError(f"profiles file not found: {path}") from None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DataFileError(f"cannot read {path}: {error}") from error
    if not isinstance(data, dict) or not data:
        raise DataFileError(f"{path.name} must hold at least one profile")
    profiles = {}
    for key, entry in data.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("weights"), dict):
            raise DataFileError(f"{path.name}: profile {key!r} has no weights")
        profiles[key] = TravellerProfile(
            key=key,
            description=str(entry.get("description", "")),
            weights=dict(entry["weights"]),
        )
    return profiles
