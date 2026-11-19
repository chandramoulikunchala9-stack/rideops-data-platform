"""Storage CLI.

    python -m rideops.storage sync --dry-run      show what would be uploaded to S3
    python -m rideops.storage sync                upload data/landing to the raw bucket
    python -m rideops.storage ls landing/bookings list objects under a prefix

Bucket and region come from RIDEOPS_S3_RAW_BUCKET and AWS_REGION; credentials from the default
AWS chain (AWS_PROFILE / SSO / instance role). Uploading costs a few cents at most for this data
volume; see docs/aws-setup.md.
"""

import argparse
import os
import sys

from rideops.config import get_settings
from rideops.logging_config import configure_logging
from rideops.storage.backends import S3Storage, sync_directory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rideops.storage", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    sub = parser.add_subparsers(dest="command", required=True)
    sync = sub.add_parser("sync", help="upload landing files")
    sync.add_argument("--dry-run", action="store_true")
    ls = sub.add_parser("ls", help="list objects")
    ls.add_argument("prefix", nargs="?", default="landing/")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    bucket = os.environ.get("RIDEOPS_S3_RAW_BUCKET")
    if not bucket:
        print("Set RIDEOPS_S3_RAW_BUCKET (Terraform output `raw_bucket`) first.", file=sys.stderr)
        return 2
    storage = S3Storage(bucket, region=os.environ.get("AWS_REGION"))

    if args.command == "sync":
        result = sync_directory(settings.data_dir, "landing", storage, dry_run=args.dry_run)
        verb = "would upload" if args.dry_run else "uploaded"
        print(f"{verb} {len(result.uploaded)} files, unchanged {len(result.skipped)}")
    else:
        for key in storage.list_keys(args.prefix):
            print(key)
    return 0


if __name__ == "__main__":
    sys.exit(main())
