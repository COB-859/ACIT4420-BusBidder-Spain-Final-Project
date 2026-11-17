"""Tests for the data generator in tools/generate_data.py."""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
GENERATED_FILES = ("companies.csv", "destinations.csv", "departures.csv", "fares.csv")


class GeneratorTests(unittest.TestCase):
    def test_generator_writes_the_same_files_as_the_ones_in_data(self):
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run(
                [sys.executable, str(PROJECT / "tools" / "generate_data.py"), "--out", folder],
                check=True, capture_output=True, cwd=PROJECT,
            )
            for name in GENERATED_FILES:
                with self.subTest(file=name):
                    # Bytes are compared, so a changed seed, value or line ending is noticed.
                    self.assertEqual((Path(folder) / name).read_bytes(),
                                     (PROJECT / "data" / name).read_bytes())


if __name__ == "__main__":
    unittest.main()
