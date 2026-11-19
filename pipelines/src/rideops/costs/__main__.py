"""Cost CLI.

python -m rideops.costs load-cur ./cur-export/          local CUR 2.0 files (csv / csv.gz)
python -m rideops.costs load-cur s3://bucket/prefix/    straight from the export bucket
"""

import argparse
import sys
from pathlib import Path

from rideops.config import get_settings
from rideops.costs.cur_loader import aggregate, load_path, read_cur_csv, store
from rideops.db.connection import connect
from rideops.logging_config import configure_logging


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rideops.costs", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    sub = parser.add_subparsers(dest="command", required=True)
    load = sub.add_parser("load-cur", help="load AWS CUR 2.0 files into ops.cloud_costs")
    load.add_argument("location", help="local file/folder or s3://bucket/prefix")
    args = parser.parse_args(argv)
    configure_logging(get_settings().log_level)

    if args.location.startswith("s3://"):
        from rideops.storage.backends import S3Storage

        bucket, _, prefix = args.location[5:].partition("/")
        storage = S3Storage(bucket)
        items = []
        for key in storage.list_keys(prefix):
            if key.endswith((".csv", ".csv.gz")):
                items.extend(read_cur_csv(storage.read_bytes(key)))
    else:
        items = load_path(Path(args.location))

    rows = aggregate(items)
    with connect() as conn:
        print(f"Loaded {store(conn, rows)} daily cost rows (source = aws_cur)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
