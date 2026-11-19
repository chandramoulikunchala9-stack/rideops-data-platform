"""Load AWS Cost and Usage Report 2.0 (Data Exports) files into ops.cloud_costs.

This is the path for REAL AWS billing data. Rows are written with source = 'aws_cur', next to
(never mixed with) the simulated rows, and the API/UI show the source on every figure.

CUR 2.0 delivers CSV (optionally gzip) with one row per line item. We aggregate to one row per
(usage day, service, usage type, workload tag, environment tag), the same grain as the simulated
data, so the same reports work for both. Tags come from the `resource_tags` map column, where a
cost-allocation tag `workload` appears as key `user_workload`.

Setup (infra/terraform, enable_cur_export = true) creates the export and its bucket. Billing
data is delivered up to 3 times a day and restated until the month closes, so loading replaces
existing rows for the same keys (upsert) instead of appending duplicates.
"""

import csv
import gzip
import hashlib
import io
import json
import logging
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import psycopg

logger = logging.getLogger(__name__)

SERVICE_NAMES = {
    "AmazonS3": "Amazon S3",
    "AmazonEC2": "Amazon EC2",
    "AWSDataTransfer": "AWS Data Transfer",
    "AmazonRDS": "Amazon RDS",
}


@dataclass(frozen=True)
class CostRow:
    cost_id: str
    usage_date: date
    provider: str
    service: str
    workload: str | None
    environment: str
    usage_type: str
    usage_quantity: Decimal
    usage_unit: str
    unit_price_usd: Decimal
    cost_usd: Decimal


def read_cur_csv(content: bytes) -> list[dict[str, str]]:
    if content[:2] == b"\x1f\x8b":  # gzip magic number
        content = gzip.decompress(content)
    return list(csv.DictReader(io.StringIO(content.decode("utf-8"))))


def _tags(raw: str) -> dict[str, str]:
    try:
        return json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}


def aggregate(line_items: Iterable[dict[str, str]]) -> list[CostRow]:
    """Group CUR line items into daily cost rows. Tax and other non-usage lines keep their cost."""
    totals: dict[tuple, list[Decimal]] = defaultdict(lambda: [Decimal(0), Decimal(0)])
    for item in line_items:
        tags = _tags(item.get("resource_tags", ""))
        key = (
            date.fromisoformat(item["line_item_usage_start_date"][:10]),
            SERVICE_NAMES.get(item["line_item_product_code"], item["line_item_product_code"]),
            item["line_item_usage_type"] or item["line_item_line_item_type"],
            tags.get("user_workload"),  # missing tag -> NULL -> reported as UNTAGGED
            tags.get("user_environment", "unknown"),
            item.get("pricing_unit") or "n/a",
        )
        totals[key][0] += Decimal(item["line_item_usage_amount"] or 0)
        totals[key][1] += Decimal(item["line_item_unblended_cost"] or 0)

    rows = []
    for (day, service, usage_type, workload, env, unit), (qty, cost) in sorted(
        totals.items(), key=lambda kv: tuple(str(k) for k in kv[0])
    ):
        digest = hashlib.sha256(
            f"{day}|{service}|{usage_type}|{workload}|{env}".encode()
        ).hexdigest()
        rows.append(CostRow(
            cost_id=f"CUR-{digest[:20]}", usage_date=day, provider="aws", service=service,
            workload=workload, environment=env, usage_type=usage_type,
            usage_quantity=qty.quantize(Decimal("0.0001")), usage_unit=unit,
            # unit price is derived so that quantity x price = cost (the reconciliation rule)
            unit_price_usd=(cost / qty).quantize(Decimal("0.000001")) if qty else Decimal(0),
            cost_usd=cost.quantize(Decimal("0.0001")),
        ))  # fmt: skip
    return rows


def store(conn: psycopg.Connection, rows: list[CostRow]) -> int:
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO ops.cloud_costs (cost_id, usage_date, provider, service, workload,
                environment, usage_type, usage_quantity, usage_unit, unit_price_usd, cost_usd,
                currency, source, _loaded_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'USD', 'aws_cur', now())
            ON CONFLICT (cost_id) DO UPDATE SET     -- CUR restates the month: latest wins
                usage_quantity = excluded.usage_quantity, unit_price_usd = excluded.unit_price_usd,
                cost_usd = excluded.cost_usd, _loaded_at = now()
            """,
            [(r.cost_id, r.usage_date, r.provider, r.service, r.workload, r.environment,
              r.usage_type, r.usage_quantity, r.usage_unit, r.unit_price_usd, r.cost_usd)
             for r in rows],
        )  # fmt: skip
    return len(rows)


def load_path(path: Path) -> list[dict[str, str]]:
    files = [path] if path.is_file() else sorted([*path.rglob("*.csv"), *path.rglob("*.csv.gz")])
    items: list[dict[str, str]] = []
    for file in files:
        items.extend(read_cur_csv(file.read_bytes()))
    logger.info("Read %d CUR line items from %d file(s)", len(items), len(files))
    return items
