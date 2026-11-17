"""Tests for the log file configuration."""

import tempfile
import unittest
from pathlib import Path

from busbidder.logsetup import LOG_FILE_NAME, close_logging, configure_logging


class ConfigureLoggingTests(unittest.TestCase):
    """Messages reach the log file, once."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.addCleanup(close_logging)
        self.log_dir = Path(self.temp_dir.name) / "logs"

    def read_log(self) -> str:
        return (self.log_dir / LOG_FILE_NAME).read_text(encoding="utf-8")

    def test_creates_the_folder_and_writes_a_message(self):
        logger = configure_logging(self.log_dir)
        logger.info("loaded 3 companies")
        close_logging()
        text = self.read_log()
        self.assertIn("INFO busbidder: loaded 3 companies", text)

    def test_warning_and_error_levels_are_written(self):
        logger = configure_logging(self.log_dir)
        logger.warning("row rejected")
        logger.error("file missing")
        close_logging()
        text = self.read_log()
        self.assertIn("WARNING busbidder: row rejected", text)
        self.assertIn("ERROR busbidder: file missing", text)

    def test_debug_messages_are_left_out(self):
        logger = configure_logging(self.log_dir)
        logger.debug("too detailed")
        close_logging()
        self.assertNotIn("too detailed", self.read_log())

    def test_configuring_twice_does_not_duplicate_messages(self):
        configure_logging(self.log_dir)
        logger = configure_logging(self.log_dir)
        logger.info("only once")
        close_logging()
        self.assertEqual(self.read_log().count("only once"), 1)


if __name__ == "__main__":
    unittest.main()
