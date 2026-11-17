"""Command-line interface: the only module that prints."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from busbidder import __version__
from busbidder.exceptions import BusBidderError
from busbidder.logsetup import close_logging, configure_logging

EXIT_OK = 0
EXIT_ERROR = 1
DEFAULT_LOG_DIR = Path("logs")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the program."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="BusBidder Spain: filter, rank and book simulated bus offers.",
    )
    parser.add_argument("--version", action="version", version=f"BusBidder Spain {__version__}")
    parser.add_argument(
        "--logs",
        type=Path,
        default=DEFAULT_LOG_DIR,
        help="folder for the log file (default: logs)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the program and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging(args.logs)
    try:
        logger.info("Program started")
        parser.print_help()
        return EXIT_OK
    except BusBidderError as error:
        logger.error("%s", error)
        print(f"Error: {error}")
        return EXIT_ERROR
    finally:
        close_logging()
