"""Tests for the command-line interface."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from busbidder.cli import EXIT_ERROR, EXIT_OK, main
from tests.helpers import SMALL_DATA, write_data_dir


class CliTestCase(unittest.TestCase):
    """Runs the program against a small data folder in a temporary place."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.folder = Path(temp.name)
        self.data_dir = write_data_dir(self.folder / "data")
        self.log_dir = self.folder / "logs"
        self.output_dir = self.folder / "output"

    def run_main(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        folders = ["--data", str(self.data_dir), "--logs", str(self.log_dir),
                   "--output", str(self.output_dir)]
        with contextlib.redirect_stdout(output):
            code = main([*folders, *arguments])
        return code, output.getvalue()

    def read_log(self) -> str:
        return (self.log_dir / "busbidder.log").read_text(encoding="utf-8")


class StartTests(CliTestCase):
    def test_no_command_prints_help_and_succeeds(self):
        code, text = self.run_main()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("BusBidder Spain", text)

    def test_version_option_exits_with_zero(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("BusBidder Spain", output.getvalue())


class ValidateTests(CliTestCase):
    def test_reports_counts_for_valid_data(self):
        code, text = self.run_main("validate")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Departures:   4 accepted (1 sold out", text)
        self.assertIn("Fares:        6 accepted", text)
        self.assertIn("Rejected rows: 0", text)

    def test_lists_rejected_rows_and_writes_the_file(self):
        fares = SMALL_DATA["fares.csv"].replace("25.00", "-25.00")
        write_data_dir(self.data_dir, fares_csv=fares)
        code, text = self.run_main("validate")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("fares.csv, line 2: price must be above 0", text)
        saved = (self.output_dir / "rejected_records.txt").read_text(encoding="utf-8")
        self.assertIn("fares.csv, line 2", saved)

    def test_rejection_and_sold_out_are_written_to_the_log(self):
        fares = SMALL_DATA["fares.csv"].replace("25.00", "-25.00")
        write_data_dir(self.data_dir, fares_csv=fares)
        self.run_main("validate")
        log = self.read_log()
        self.assertIn("WARNING busbidder.loader: Rejected fares.csv, line 2", log)
        self.assertIn("1 departures are sold out", log)
        self.assertIn("INFO busbidder: Command: validate", log)

    def test_missing_data_file_gives_an_error_message_and_code(self):
        (self.data_dir / "departures.csv").unlink()
        code, text = self.run_main("validate")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Error: data file not found", text)
        self.assertIn("ERROR busbidder: data file not found", self.read_log())

    def test_folder_options_also_work_after_the_command(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["validate", "--data", str(self.data_dir), "--logs", str(self.log_dir),
                         "--output", str(self.output_dir)])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Fares:        6 accepted", output.getvalue())


if __name__ == "__main__":
    unittest.main()
