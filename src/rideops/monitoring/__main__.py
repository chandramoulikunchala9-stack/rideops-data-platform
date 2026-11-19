"""Monitoring CLI.

python -m rideops.monitoring alerts              evaluate alert rules, store and notify
python -m rideops.monitoring seed-incidents      load monitoring/incidents/*.md into ops.incidents
python -m rideops.monitoring simulate-incident   write a schema-drifted landing file (practice)
python -m rideops.monitoring resolve-incident    runbook fix: quarantine the bad file
"""

import argparse
import logging
import sys

from rideops.config import ApiSettings, get_settings
from rideops.db.connection import connect
from rideops.logging_config import configure_logging
from rideops.monitoring import alerts, incidents

logger = logging.getLogger("rideops.monitoring")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rideops.monitoring", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)  # fmt: skip
    parser.add_argument(
        "command", choices=["alerts", "seed-incidents", "simulate-incident", "resolve-incident"]
    )
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    if args.command == "simulate-incident":
        path = incidents.simulate_schema_drift(settings.data_dir)
        print(f"Wrote {path}. Now run: python -m rideops.db all  (then check alerts)")
        return 0

    api = ApiSettings()
    with connect() as conn:
        if args.command == "alerts":
            result = alerts.run_alerting(conn, api.monthly_budget_usd, api.alert_webhook_url)
            logger.info("alerting: %s", result)
        elif args.command == "seed-incidents":
            logger.info("seeded %d incidents", incidents.seed_incidents(conn))
        elif args.command == "resolve-incident":
            incidents.resolve_schema_drift(conn, settings.data_dir)
            logger.info(
                "Resolved. Re-run: python -m rideops.db quality && python -m rideops.monitoring alerts"
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
