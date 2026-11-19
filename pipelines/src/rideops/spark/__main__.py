"""Lakehouse CLI (run inside the spark container or any environment with PySpark + Delta).

    python -m rideops.spark bronze        landing -> bronze (incremental, exactly-once)
    python -m rideops.spark silver        bronze -> silver (validate, quarantine, MERGE)
    python -m rideops.spark gold          silver -> gold (CDF-driven incremental)
    python -m rideops.spark stream        ride events: landing -> bronze -> windowed gold
    python -m rideops.spark quality       lakehouse data-quality expectations
    python -m rideops.spark export        gold -> PostgreSQL schema `lakehouse`
    python -m rideops.spark all           everything above, in order
    python -m rideops.spark delta-demo    guided tour of Delta Lake features
    python -m rideops.spark replay        simulate a live ride-event feed (for --live streaming)

Pipeline names match reference.PIPELINES, so real runs appear next to the simulated history.
"""

import argparse
import logging
import sys

from rideops.config import get_settings
from rideops.logging_config import configure_logging
from rideops.monitoring.tracking import open_tracker
from rideops.spark.lakehouse import Lakehouse

logger = logging.getLogger("rideops.spark")

BRONZE_GROUPS = {
    "ingest_reference_data": ("vehicles", "drivers", "customers"),
    "ingest_bookings": ("bookings",),
    "ingest_payments": ("payments",),
    "ingest_app_events": ("app_events",),
}


def run_bronze(spark, lake: Lakehouse) -> None:
    from rideops.spark import bronze

    for pipeline, entities in BRONZE_GROUPS.items():
        with open_tracker(pipeline, layer="bronze") as run:
            for entity in entities:
                rows = bronze.ingest(spark, lake, entity)
                run.add_counts(read=rows, written=rows)
                run.log("INFO", f"bronze {entity}: +{rows} rows")


def run_silver(spark, lake: Lakehouse) -> None:
    from rideops.spark import silver

    with open_tracker("silver_transform", layer="silver") as run:
        counts = silver.process_all(spark, lake)
        run.add_counts(**counts)
        run.log("INFO", f"silver: {counts}")


def run_gold(spark, lake: Lakehouse) -> None:
    from rideops.spark import gold

    with open_tracker("gold_aggregations", layer="gold") as run:
        counts = gold.build_gold(spark, lake)
        run.add_counts(**counts)
        run.log("INFO", f"gold: {counts}")


def run_stream(spark, lake: Lakehouse, live: bool) -> None:
    from rideops.spark import streaming

    with open_tracker("stream_ride_events", layer="bronze") as run:
        counts = streaming.run(spark, lake, live=live)
        run.add_counts(**counts)
        run.log("INFO", f"stream: {counts} (rejected = dropped by watermark)")


def run_quality(spark, lake: Lakehouse) -> None:
    from rideops.spark import quality

    with open_tracker("data_quality_checks", layer="silver") as run:
        results = quality.run_expectations(spark, lake)
        quality.store_results(results, run.run_id)
        failed = [r.check_name for r in results if r.status == "FAIL"]
        run.add_counts(read=len(results), written=len(results), rejected=len(failed))
        run.log("WARNING" if failed else "INFO", f"failed expectations: {failed or 'none'}")


def run_export(spark, lake: Lakehouse) -> None:
    from rideops.spark import export

    with open_tracker("export_to_postgres", layer="serving") as run:
        counts = export.export_gold(spark, lake)
        run.add_counts(**counts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m rideops.spark",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        choices=[
            "bronze",
            "silver",
            "gold",
            "stream",
            "quality",
            "export",
            "all",
            "delta-demo",
            "replay",
        ],  # fmt: skip
    )
    parser.add_argument("--live", action="store_true", help="stream: run continuously")
    parser.add_argument("--batches", type=int, default=20, help="replay: number of batches")
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(settings.log_level)
    lake = Lakehouse(settings.data_dir)

    if args.command == "replay":
        from rideops.spark.streaming import replay_events

        replay_events(lake, limit_batches=args.batches)
        return 0

    from rideops.spark.session import get_spark

    spark = get_spark(f"rideops-{args.command}")
    try:
        if args.command == "delta-demo":
            from rideops.spark.delta_demo import run_demo

            facts = run_demo(spark, str(lake.root / "_demo" / "delta_features"))
            logger.info("delta-demo facts: %s", facts)
            return 0
        steps = {
            "bronze": [run_bronze], "silver": [run_silver], "gold": [run_gold],
            "stream": [lambda s, lk: run_stream(s, lk, args.live)], "quality": [run_quality],
            "export": [run_export],
            "all": [run_bronze, run_silver, run_gold,
                    lambda s, lk: run_stream(s, lk, False), run_quality, run_export],
        }[args.command]  # fmt: skip
        for step in steps:
            step(spark, lake)
    except Exception:
        logger.exception("Lakehouse job '%s' failed", args.command)
        return 1
    finally:
        spark.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
