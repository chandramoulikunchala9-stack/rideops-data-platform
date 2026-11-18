"""Command-line entry point.

Usage:
    python -m rideops.generators                      # uses settings from env / .env
    python -m rideops.generators --scale full --seed 7
    python -m rideops.generators --help
"""

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

from rideops.config import get_settings
from rideops.generators.runner import RunConfig, run
from rideops.generators.scale import SCALE_PROFILES, get_scale_profile
from rideops.logging_config import configure_logging

logger = logging.getLogger("rideops.generators")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rideops.generators",
        description="Generate synthetic RideOps source data into the landing zone.",
    )
    # Defaults are None so we can tell "not given" apart and fall back to Settings.
    parser.add_argument("--scale", choices=sorted(SCALE_PROFILES), help="dataset size preset")
    parser.add_argument("--seed", type=int, help="random seed (same seed -> same data)")
    parser.add_argument("--end-date", type=date.fromisoformat, help="last day of data, YYYY-MM-DD")
    parser.add_argument("--data-dir", type=Path, help="root data directory")
    parser.add_argument("--batch-id", default="0001", help="batch identifier used in file names")
    return parser


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    args = build_parser().parse_args(argv)
    configure_logging(settings.log_level)

    config = RunConfig(
        profile=get_scale_profile(args.scale or settings.scale),
        seed=args.seed if args.seed is not None else settings.seed,
        end_date=args.end_date or settings.data_end_date,
        data_dir=args.data_dir or settings.data_dir,
        batch_id=args.batch_id,
    )
    try:
        run(config)
    except OSError:
        # e.g. disk full or permission denied: log the full traceback, return a failure exit code
        logger.exception("Data generation failed while writing files")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
