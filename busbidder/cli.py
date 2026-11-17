"""Command-line interface: the only module that prints."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from busbidder import __version__
from busbidder.exceptions import BusBidderError
from busbidder.loader import REJECTED_FILE, LoadResult, load_all, write_rejected
from busbidder.logsetup import close_logging, configure_logging

EXIT_OK = 0
EXIT_ERROR = 1
DEFAULT_DATA_DIR = Path("data")
DEFAULT_LOG_DIR = Path("logs")
DEFAULT_OUTPUT_DIR = Path("output")


def _add_folder_options(parser: argparse.ArgumentParser, top_level: bool) -> None:
    """Add the folder options, so they work before or after the command name."""

    def default(value: Path) -> Path | str:
        return value if top_level else argparse.SUPPRESS

    parser.add_argument("--data", type=Path, default=default(DEFAULT_DATA_DIR),
                        help="folder with the data files (default: data)")
    parser.add_argument("--logs", type=Path, default=default(DEFAULT_LOG_DIR),
                        help="folder for the log file (default: logs)")
    parser.add_argument("--output", type=Path, default=default(DEFAULT_OUTPUT_DIR),
                        help="folder for report files (default: output)")


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser for the program."""
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="BusBidder Spain: filter, rank and book simulated bus offers.",
    )
    parser.add_argument("--version", action="version", version=f"BusBidder Spain {__version__}")
    _add_folder_options(parser, top_level=True)
    commands = parser.add_subparsers(dest="command", metavar="command")
    validate = commands.add_parser("validate", help="check the data files and list rejected rows")
    _add_folder_options(validate, top_level=False)
    return parser


def load_data(args: argparse.Namespace) -> LoadResult:
    """Read the data files and write the list of rejected rows."""
    result = load_all(args.data)
    write_rejected(result.rejected, args.output / REJECTED_FILE)
    return result


def run_validate(args: argparse.Namespace) -> int:
    """Print what was accepted and what was set aside."""
    result = load_data(args)
    print("Data check")
    print(f"  Companies:    {len(result.companies)} accepted")
    print(f"  Destinations: {len(result.destinations)} accepted")
    print(f"  Departures:   {len(result.departures)} accepted "
          f"({len(result.sold_out())} sold out, kept and hidden from searches)")
    print(f"  Fares:        {len(result.fares)} accepted")
    print(f"  Rejected rows: {len(result.rejected)} (saved in {args.output / REJECTED_FILE})")
    for record in result.rejected:
        print(f"    {record.describe()}")
    return EXIT_OK


COMMANDS = {"validate": run_validate}


def main(argv: Sequence[str] | None = None) -> int:
    """Run the program and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    logger = configure_logging(args.logs)
    try:
        logger.info("Command: %s", args.command or "(none)")
        if args.command is None:
            parser.print_help()
            return EXIT_OK
        return COMMANDS[args.command](args)
    except BusBidderError as error:
        logger.error("%s", error)
        print(f"Error: {error}")
        return EXIT_ERROR
    finally:
        close_logging()
