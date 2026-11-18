"""Orchestrates a generation run: generate every entity, write the files, write a manifest."""

import json
import logging
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from rideops.generators.bookings import apply_booking_defects, build_bookings
from rideops.generators.common import EntityBatch
from rideops.generators.defects import DefectRates
from rideops.generators.entities import generate_customers, generate_drivers, generate_vehicles
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


def generate_all(config: RunConfig) -> list[EntityBatch]:
    """Generate entities in dependency order.

    vehicles -> drivers (need vehicles) -> customers -> bookings (need all three)
             -> payments (need clean bookings) -> booking defects (last!)
    """
    p = config.profile
    common = {
        "seed": config.seed,
        "end_date": config.end_date,
        "history_days": p.history_days,
    }
    vehicles = generate_vehicles(p.vehicles, defects=config.defects, **common)
    drivers = generate_drivers(
        p.drivers, vehicles=vehicles.records, defects=config.defects, **common
    )
    customers = generate_customers(p.customers, defects=config.defects, **common)

    booking_records = build_bookings(
        p.bookings,
        customers=customers.records,
        drivers=drivers.records,
        vehicles=vehicles.records,
        **common,
    )
    logger.info("Booking status mix: %s", dict(Counter(b["status"] for b in booking_records)))
    # Payments must be derived before booking defects are injected, otherwise a corrupted
    # booking (e.g. negative fare) would leak into the payments as a second, hidden defect.
    payments = generate_payments(booking_records, seed=config.seed, defects=config.defects)
    bookings = apply_booking_defects(booking_records, seed=config.seed, defects=config.defects)
    return [vehicles, drivers, customers, bookings, payments]


def run(config: RunConfig) -> Path:
    """Generate, write and describe one batch. Returns the manifest path."""
    started = time.perf_counter()
    logger.info(
        "Generating scale=%s seed=%s end_date=%s", config.profile.name, config.seed, config.end_date
    )

    entities: dict[str, dict] = {}
    for batch in generate_all(config):
        path = write_batch(batch, config.data_dir, config.end_date, config.batch_id)
        logger.info(
            "Wrote %-10s rows=%-7d defects=%s -> %s",
            batch.name,
            len(batch.records),
            batch.defects,
            path,
        )
        entities[batch.name] = {
            "rows": len(batch.records),
            "file": path.relative_to(config.data_dir).as_posix(),
            "format": batch.file_format,
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
