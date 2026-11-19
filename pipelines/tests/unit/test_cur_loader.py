"""CUR 2.0 parsing and aggregation, using a synthetic fixture in the CUR 2.0 column format."""

import gzip
from datetime import date
from decimal import Decimal
from pathlib import Path

from rideops.costs.cur_loader import aggregate, load_path, read_cur_csv

FIXTURE = Path(__file__).parents[1] / "fixtures" / "cur2_sample.csv"


def test_line_items_are_aggregated_per_day_service_and_tags() -> None:
    rows = {(r.usage_date, r.service, r.workload): r for r in aggregate(load_path(FIXTURE))}
    s3 = rows[(date(2026, 10, 1), "Amazon S3", "data_lake")]
    assert s3.usage_quantity == Decimal("0.75")  # two storage line items, same day, summed
    assert s3.cost_usd == Decimal("0.0188")  # 0.0125 + 0.00625, rounded to 4 dp
    assert s3.environment == "dev"


def test_missing_tags_become_untagged_and_tax_is_kept() -> None:
    rows = aggregate(load_path(FIXTURE))
    untagged = [r for r in rows if r.workload is None]
    assert {r.service for r in untagged} == {"Amazon EC2", "Amazon S3"}
    tax = next(r for r in rows if r.usage_type == "Tax")
    assert tax.cost_usd == Decimal("0.0020") and tax.unit_price_usd == 0


def test_quantity_times_price_equals_cost_and_ids_are_stable() -> None:
    first = aggregate(load_path(FIXTURE))
    for r in first:
        if r.usage_quantity:
            assert abs(r.usage_quantity * r.unit_price_usd - r.cost_usd) <= Decimal("0.01")
    assert [r.cost_id for r in first] == [r.cost_id for r in aggregate(load_path(FIXTURE))]


def test_gzip_files_are_read() -> None:
    compressed = gzip.compress(FIXTURE.read_bytes())
    assert read_cur_csv(compressed) == read_cur_csv(FIXTURE.read_bytes())
