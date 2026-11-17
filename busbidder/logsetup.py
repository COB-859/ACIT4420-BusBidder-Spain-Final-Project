"""Log file configuration: one log file shared by all modules."""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER_NAME = "busbidder"
LOG_FILE_NAME = "busbidder.log"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def configure_logging(log_dir: Path) -> logging.Logger:
    """Send the program's log messages to a file in log_dir and return the logger.

    Calling it again replaces the earlier file handler, so messages are
    never written twice.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    close_logging()
    handler = logging.FileHandler(log_dir / LOG_FILE_NAME, encoding="utf-8")
    handler.setFormatter(logging.Formatter(LOG_FORMAT, DATE_FORMAT))
    logger.addHandler(handler)
    # Keep log lines out of the terminal: only the command-line module prints.
    logger.propagate = False
    return logger


def close_logging() -> None:
    """Close and remove the log file handlers."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
