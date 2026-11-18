"""Central logging setup so every RideOps module logs in the same format."""

import logging

LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger. Call once, at program start-up.

    `force=True` replaces handlers configured earlier, so calling it twice is harmless.
    An unknown level name (e.g. "VERBOSE") raises ValueError instead of failing silently.
    """
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT, datefmt=DATE_FORMAT, force=True)
