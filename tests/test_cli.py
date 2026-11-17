"""Tests for the command-line interface."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from busbidder.cli import EXIT_OK, main


class MainTests(unittest.TestCase):
    """The program starts, prints its help and writes to the log."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.log_dir = Path(self.temp_dir.name) / "logs"

    def run_main(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(["--logs", str(self.log_dir), *arguments])
        return code, output.getvalue()

    def test_no_command_prints_help_and_succeeds(self):
        code, text = self.run_main()
        self.assertEqual(code, EXIT_OK)
        self.assertIn("BusBidder Spain", text)

    def test_start_is_recorded_in_the_log(self):
        self.run_main()
        log_text = (self.log_dir / "busbidder.log").read_text(encoding="utf-8")
        self.assertIn("Program started", log_text)

    def test_version_option_exits_with_zero(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            main(["--version"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("BusBidder Spain", output.getvalue())


if __name__ == "__main__":
    unittest.main()
