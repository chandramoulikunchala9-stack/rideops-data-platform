"""Orchestrates a generation run: generate every entity, write the files, write a manifest."""

import json
import logging
import time
from collections import Counter
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from rideops.generators.bookings import apply_booking_defects, build_bookings
from rideops.generators.common import EntityBatch
from rideops.generators.defects import DefectRates
from rideops.generators.entities import generate_customers, generate_drivers, generate_vehicles
from rideops.generators.events import generate_app_events, generate_ride_events
from rideops.generators.ops import generate_ops
from rideops.generators.payments import generate_payments
from rideops.generators.scale import ScaleProfile
from rideops.generators.writers import write_batch

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunConfig:
    profile: ScaleProfile
    seed: int
    end_date: date
    data_dir: Path
    batch_id: str = "0001"
    defects: DefectRates = DefectRates()


def generate_all(config: RunConfig) -> Iterator[EntityBatch]:
    """Yield entity batches in dependency order.

    This is a generator: each batch can be written and freed before the next one is built,
    which keeps memory flat even with hundreds of thousands of events.

        vehicles -> drivers -> customers -> clean bookings
            -> payments, ride events, app events, pipeline runs, cloud costs (all need clean
               bookings) -> booking defects (applied last)
    """
    p = config.profile
    common = {"seed": config.seed, "end_date": config.end_date, "history_days": p.history_days}
    defects = config.defects

    vehicles = generate_vehicles(p.vehicles, defects=defects, **common)
    yield vehicles
    drivers = generate_drivers(p.drivers, vehicles=vehicles.records, defects=defects, **common)
    yield drivers
    customers = generate_customers(p.customers, defects=defects, **common)
    yield customers

    bookings = build_bookings(
        p.bookings,
        customers=customers.records,
        drivers=drivers.records,
        vehicles=vehicles.records,
        **common,
    )
    logger.info("Booking status mix: %s", dict(Counter(b["status"] for b in bookings)))
    # Everything derived from bookings must use the *clean* records, so it comes first;
    # otherwise booking defects (e.g. a negative fare) would leak into other datasets.
    yield generate_payments(bookings, seed=config.seed, defects=defects)
    yield generate_ride_events(bookings, seed=config.seed, defects=defects)
    yield generate_app_events(bookings, seed=config.seed, defects=defects)
    yield from generate_ops(bookings, defects=defects, **common)
    yield apply_booking_defects(bookings, seed=config.seed, defects=defects)


def run(config: RunConfig) -> Path:
    """Generate, write and describe one batch. Returns the manifest path."""
    started = time.perf_counter()
    logger.info(
        "Generating scale=%s seed=%s end_date=%s", config.profile.name, config.seed, config.end_date
    )

    entities: dict[str, dict] = {}
    for batch in generate_all(config):
        paths = write_batch(batch, config.data_dir, config.end_date, config.batch_id)
        logger.info(
            "Wrote %-13s rows=%-7d files=%-3d defects=%s",
            batch.name,
            len(batch.records),
            len(paths),
            batch.defects,
        )
        entities[batch.name] = {
            "rows": len(batch.records),
            "format": batch.file_format,
            "files": [path.relative_to(config.data_dir).as_posix() for path in paths],
            "defects_injected": batch.defects,
        }

    manifest = {
        "batch_id": config.batch_id,
        "seed": config.seed,
        "scale": config.profile.name,
        "end_date": config.end_date.isoformat(),
        "defect_rates": asdict(config.defects),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "entities": entities,
    }
    manifest_path = (
        config.data_dir / "landing" / "_manifests" / f"generation_{config.batch_id}.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    logger.info("Done in %.2fs. Manifest: %s", time.perf_counter() - started, manifest_path)
    return manifest_path
