"""Tests for reading the data files."""

import tempfile
import unittest
from pathlib import Path

from busbidder.exceptions import DataFileError
from busbidder.loader import load_all, load_profiles, read_rows, write_rejected
from busbidder.ranking import check_weights
from tests.helpers import SMALL_DATA, write_data_dir

PROJECT_DATA = Path(__file__).resolve().parent.parent / "data"


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)


class LoadAllTests(TempDirTestCase):
    def test_normal_files_load_completely(self):
        result = load_all(write_data_dir(self.folder))
        self.assertEqual(len(result.companies), 2)
        self.assertEqual(len(result.destinations), 2)
        self.assertEqual(len(result.departures), 4)
        self.assertEqual(len(result.fares), 6)
        self.assertEqual(result.rejected, [])

    def test_values_are_converted_to_the_right_types(self):
        result = load_all(write_data_dir(self.folder))
        departure = result.departures["SR-MAL-0001"]
        self.assertEqual(departure.company.name, "SolRuta")
        self.assertEqual(departure.duration_minutes, 420)
        self.assertEqual(departure.seats, 3)
        self.assertEqual(departure.emissions_kg, 15.9)
        fare = result.fares["CE-MAL-0001-F"]
        self.assertEqual(fare.price, 60.0)
        self.assertTrue(fare.refundable)

    def test_offers_join_each_fare_with_its_departure(self):
        offers = load_all(write_data_dir(self.folder)).offers()
        self.assertEqual(len(offers), 6)
        by_id = {offer.offer_id: offer for offer in offers}
        self.assertEqual(by_id["SR-MAL-0001-S"].departure.departure_id, "SR-MAL-0001")

    def test_blank_emissions_is_loaded_as_missing(self):
        result = load_all(write_data_dir(self.folder))
        self.assertIsNone(result.departures["CE-MAL-0002"].emissions_kg)

    def test_zero_seats_is_kept_and_reported_as_sold_out(self):
        result = load_all(write_data_dir(self.folder))
        self.assertEqual([d.departure_id for d in result.sold_out()], ["CE-MAL-0002"])
        self.assertEqual(result.rejected, [])

    def test_bad_row_is_set_aside_and_loading_continues(self):
        fares = SMALL_DATA["fares.csv"].replace("25.00", "-25.00")
        result = load_all(write_data_dir(self.folder, fares_csv=fares))
        self.assertEqual(len(result.fares), 5)
        self.assertEqual(len(result.rejected), 1)
        record = result.rejected[0]
        self.assertEqual((record.source, record.line), ("fares.csv", 2))
        self.assertIn("price must be above 0", record.reason)
        self.assertIn("SR-MAL-0001-B", record.row)

    def test_fares_of_a_rejected_departure_are_rejected_too(self):
        departures = SMALL_DATA["departures.csv"].replace(
            "SR-TOL-0001,SR,TOL,2027-07-12T10:00,2027-07-12T11:00",
            "SR-TOL-0001,SR,TOL,2027-07-12T10:00,2027-07-12T09:00",
        )
        result = load_all(write_data_dir(self.folder, departures_csv=departures))
        reasons = [record.reason for record in result.rejected]
        self.assertEqual(len(reasons), 2)
        self.assertIn("is not after departure", reasons[0])
        self.assertEqual(reasons[1], "unknown departure: 'SR-TOL-0001'")

    def test_missing_main_file_is_a_data_file_error(self):
        write_data_dir(self.folder)
        (self.folder / "fares.csv").unlink()
        with self.assertRaises(DataFileError) as caught:
            load_all(self.folder)
        self.assertIn("data file not found", str(caught.exception))

    def test_missing_column_is_a_data_file_error(self):
        fares = SMALL_DATA["fares.csv"].replace("price_eur", "cost")
        with self.assertRaises(DataFileError) as caught:
            load_all(write_data_dir(self.folder, fares_csv=fares))
        self.assertIn("has no column: price_eur", str(caught.exception))

    def test_empty_file_is_a_data_file_error(self):
        with self.assertRaises(DataFileError):
            load_all(write_data_dir(self.folder, companies_csv=""))

    def test_main_file_with_header_only_is_a_data_file_error(self):
        header = SMALL_DATA["fares.csv"].splitlines()[0] + "\n"
        with self.assertRaises(DataFileError) as caught:
            load_all(write_data_dir(self.folder, fares_csv=header))
        self.assertIn("fares.csv holds no valid rows", str(caught.exception))

    def test_emptied_main_file_is_not_replaced_by_the_invalid_examples(self):
        write_data_dir(self.folder, companies_csv="company_code,name,rating\n")
        (self.folder / "companies_invalid.csv").write_text(
            "company_code,name,rating\nSR,SolRuta Copy,3.0\n", encoding="utf-8")
        with self.assertRaises(DataFileError) as caught:
            load_all(self.folder)
        self.assertIn("companies.csv holds no valid rows", str(caught.exception))

    def test_directory_in_place_of_a_file_is_a_data_file_error(self):
        write_data_dir(self.folder)
        (self.folder / "fares.csv").unlink()
        (self.folder / "fares.csv").mkdir()
        with self.assertRaises(DataFileError) as caught:
            load_all(self.folder)
        self.assertIn("cannot read", str(caught.exception))

    def test_short_row_is_rejected_as_missing_value(self):
        fares = SMALL_DATA["fares.csv"] + "SR-TOL-0001-S,SR-TOL-0001,standard\n"
        result = load_all(write_data_dir(self.folder, fares_csv=fares))
        self.assertEqual(
            result.rejected[0].reason,
            "row has too few values: nothing for price_eur, changeable, refundable, "
            "change_fee_eur, refund_percent, luggage_kg",
        )

    def test_row_with_too_many_values_is_rejected(self):
        # A decimal comma splits 19,2 into two cells.
        departures = SMALL_DATA["departures.csv"] + (
            "SR-MAL-0002,SR,MAL,2027-07-12T08:00,2027-07-12T14:00,0,30,19,2\n")
        result = load_all(write_data_dir(self.folder, departures_csv=departures))
        self.assertNotIn("SR-MAL-0002", result.departures)
        self.assertEqual(result.rejected[0].reason, "row has 1 value(s) too many")

    def test_numbers_written_in_unusual_ways_are_rejected(self):
        for seats in ("3_7", "1e1", "\u0663"):
            with self.subTest(seats=seats):
                departures = SMALL_DATA["departures.csv"].replace(
                    "2027-07-12T15:00,1,3,15.9", f"2027-07-12T15:00,1,{seats},15.9")
                folder = self.folder / f"case_{len(seats)}_{ord(seats[0])}"
                result = load_all(write_data_dir(folder, departures_csv=departures))
                self.assertIn("seats is not a whole number", result.rejected[0].reason)

    def test_unknown_company_and_destination_are_rejected(self):
        departures = SMALL_DATA["departures.csv"] + (
            "ZZ-MAL-0001,ZZ,MAL,2027-07-12T08:00,2027-07-12T14:00,0,30,15.0\n"
            "SR-XXX-0001,SR,XXX,2027-07-12T08:00,2027-07-12T14:00,0,30,15.0\n")
        result = load_all(write_data_dir(self.folder, departures_csv=departures))
        self.assertEqual([record.reason for record in result.rejected],
                         ["unknown company: 'ZZ'", "unknown destination: 'XXX'"])

    def test_missing_optional_file_is_noted_in_the_log(self):
        with self.assertLogs("busbidder.loader", level="INFO") as logs:
            load_all(write_data_dir(self.folder))
        self.assertTrue(any("Optional file fares_invalid.csv not found" in line for line in logs.output))

    def test_invalid_file_is_read_after_the_main_file(self):
        write_data_dir(self.folder)
        (self.folder / "fares_invalid.csv").write_text(
            SMALL_DATA["fares.csv"].splitlines()[0] + "\n"
            "SR-MAL-0001-B,SR-MAL-0001,basic,19.00,no,no,0,0,10\n",
            encoding="utf-8",
        )
        result = load_all(self.folder)
        self.assertEqual(result.fares["SR-MAL-0001-B"].price, 25.0)
        self.assertEqual(result.rejected[0].source, "fares_invalid.csv")
        self.assertEqual(result.rejected[0].reason, "duplicate offer ID: SR-MAL-0001-B")

    def test_file_saved_with_a_byte_order_mark_is_read(self):
        write_data_dir(self.folder)
        (self.folder / "companies.csv").write_text(SMALL_DATA["companies.csv"], encoding="utf-8-sig")
        self.assertEqual(len(load_all(self.folder).companies), 2)

    def test_read_rows_reports_line_numbers(self):
        write_data_dir(self.folder)
        rows = read_rows(self.folder / "companies.csv", ("company_code",))
        self.assertEqual([line for line, _row in rows], [2, 3])


class ProjectDataTests(unittest.TestCase):
    """The data files shipped with the project."""

    EXPECTED_REJECTIONS = {
        ("companies_invalid.csv", 2): "rating is above 5",
        ("companies_invalid.csv", 3): "duplicate company code: SR",
        ("companies_invalid.csv", 4): "company name is missing",
        ("departures_invalid.csv", 2): "unknown destination: 'XXX'",
        ("departures_invalid.csv", 3): "unknown company: 'ZZ'",
        ("departures_invalid.csv", 4): "departure is not a real date and time",
        ("departures_invalid.csv", 5): "is not after departure",
        ("departures_invalid.csv", 6): "seats is below 0",
        ("departures_invalid.csv", 7): "transfers is missing",
        ("departures_invalid.csv", 8): "departure ID must look like SR-MAL-0001",
        ("departures_invalid.csv", 9): "does not match company CE",
        ("departures_invalid.csv", 10): "duplicate departure ID: SR-MAL-0001",
        ("departures_invalid.csv", 11): "emissions is below 0",
        ("departures_invalid.csv", 12): "journey is longer than 24 hours",
        ("departures_invalid.csv", 13): "transfers is not a whole number",
        ("fares_invalid.csv", 2): "price must be above 0",
        ("fares_invalid.csv", 3): "unknown departure: 'SR-MAL-9999'",
        ("fares_invalid.csv", 4): "offer ID must look like SR-MAL-0001-B",
        ("fares_invalid.csv", 5): "does not match departure SR-MAL-0002",
        ("fares_invalid.csv", 6): "duplicate offer ID: SR-MAL-0001-B",
        ("fares_invalid.csv", 7): "changeable must be yes or no",
        ("fares_invalid.csv", 8): "refund percent is outside 0 to 100",
        ("fares_invalid.csv", 9): "does not match fare type flex",
        ("fares_invalid.csv", 10): "price is missing",
        ("fares_invalid.csv", 11): "refundable is no but refund percent is 80",
        ("fares_invalid.csv", 12): "price is not a number",
    }

    @classmethod
    def setUpClass(cls):
        cls.result = load_all(PROJECT_DATA)

    def test_minimum_dataset_is_present(self):
        self.assertEqual(len(self.result.companies), 5)
        self.assertEqual(len(self.result.destinations), 15)
        pairs = {(d.company.code, d.destination.code) for d in self.result.departures.values()}
        self.assertEqual(len(pairs), 75)

    def test_every_hand_written_invalid_row_is_rejected_for_the_right_reason(self):
        found = {(record.source, record.line): record.reason for record in self.result.rejected}
        self.assertEqual(sorted(found), sorted(self.EXPECTED_REJECTIONS))
        for place, expected in self.EXPECTED_REJECTIONS.items():
            with self.subTest(place=place):
                self.assertIn(expected, found[place])

    def test_no_row_of_the_main_files_is_rejected(self):
        sources = {record.source for record in self.result.rejected}
        self.assertTrue(all(source.endswith("_invalid.csv") for source in sources), sources)

    def test_data_has_sold_out_departures_and_missing_emissions(self):
        self.assertGreater(len(self.result.sold_out()), 0)
        missing = [d for d in self.result.departures.values() if d.emissions_kg is None]
        self.assertGreater(len(missing), 0)


class RejectedFileTests(TempDirTestCase):
    def test_rejected_rows_are_written_with_place_and_reason(self):
        fares = SMALL_DATA["fares.csv"].replace("25.00", "-25.00")
        result = load_all(write_data_dir(self.folder / "data", fares_csv=fares))
        target = self.folder / "output" / "rejected_records.txt"
        write_rejected(result.rejected, target)
        text = target.read_text(encoding="utf-8")
        self.assertIn("Rejected rows: 1", text)
        self.assertIn("fares.csv, line 2: price must be above 0", text)
        self.assertIn("row: SR-MAL-0001-B,SR-MAL-0001,basic,-25.00", text)

    def test_file_is_replaced_on_each_run(self):
        target = self.folder / "rejected_records.txt"
        target.write_text("old content\n", encoding="utf-8")
        write_rejected([], target)
        text = target.read_text(encoding="utf-8")
        self.assertNotIn("old content", text)
        self.assertIn("Rejected rows: 0", text)


class ProfileTests(TempDirTestCase):
    def test_profiles_are_loaded(self):
        write_data_dir(self.folder)
        profiles = load_profiles(self.folder / "profiles.json")
        self.assertEqual(sorted(profiles), ["budget", "flexible"])
        self.assertEqual(profiles["budget"].weights["price"], 55)
        self.assertEqual(profiles["flexible"].description, "Flexibility first")

    def test_project_profiles_include_the_three_required_ones(self):
        profiles = load_profiles(PROJECT_DATA / "profiles.json")
        for key in ("budget", "business", "flexible", "eco"):
            self.assertIn(key, profiles)

    def test_every_project_profile_has_usable_weights(self):
        for key, profile in load_profiles(PROJECT_DATA / "profiles.json").items():
            with self.subTest(profile=key):
                weights = check_weights(profile.weights)
                self.assertAlmostEqual(sum(weights.values()), 100)

    def test_missing_or_broken_profiles_file_is_a_data_file_error(self):
        with self.assertRaises(DataFileError):
            load_profiles(self.folder / "profiles.json")
        for text in ("{not json", "[]", "{}", '{"budget": {"description": "no weights"}}'):
            (self.folder / "profiles.json").write_text(text, encoding="utf-8")
            with self.subTest(text=text), self.assertRaises(DataFileError):
                load_profiles(self.folder / "profiles.json")


if __name__ == "__main__":
    unittest.main()
