"""BusBidder Spain: filter, rank and book simulated bus offers."""

import logging

__version__ = "0.1.0"

# Without this, warnings logged before a log file is set up (for example
# in tests) would be printed to the terminal.
logging.getLogger("busbidder").addHandler(logging.NullHandler())
