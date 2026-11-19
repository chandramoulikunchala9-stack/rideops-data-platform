# SQL Query Optimisation Notes

All plans below were captured on 2026-11-18 with `EXPLAIN (ANALYZE, COSTS OFF)` on the local
PostgreSQL 17.5 container. The data was the `full` synthetic dataset: 99,187 bookings and 85k payments.
To compare "without index", each index was dropped **inside a transaction and rolled back**, so the
schema was never actually changed. (PostgreSQL DDL is transactional.)

Reproduce:

```bash
docker exec -it rideops-postgres psql -U rideops -d rideops
BEGIN; DROP INDEX core.ix_bookings_customer_requested;
EXPLAIN (ANALYZE, COSTS OFF) SELECT ... ;
ROLLBACK;
```

## Q1: a customer's recent bookings (composite index)

```sql
SELECT booking_id, requested_at, status, fare_amount
FROM core.bookings
WHERE customer_id = 'CUS-004242' AND requested_at >= '2026-10-01'
ORDER BY requested_at DESC;
```

| | Plan | Execution time |
|---|---|---|
| Without index | `Seq Scan on bookings`: reads all ~99k rows to return 2 | 9.15 ms |
| With `ix_bookings_customer_requested (customer_id, requested_at)` | `Bitmap Index Scan`, `Index Cond` on **both** columns | 0.19 ms |

**Column order matters.** The equality column (`customer_id`) comes first and the range column
(`requested_at`) second. With the order reversed, the index could only use the date range and would
scan every customer's October bookings.

## Q2: a join for one day and one city (two indexes working together)

```sql
SELECT b.booking_id, p.status
FROM core.bookings b
JOIN core.payments p ON p.booking_id = b.booking_id
WHERE b.booking_date = '2026-10-15' AND b.city = 'HYD';
```

| | Plan | Execution time |
|---|---|---|
| Without indexes | `Hash Join`: seq scan of 85k payments, plus a seq scan of bookings | 21.70 ms |
| With `ix_bookings_booking_date_city` and `ix_payments_booking` | `Nested Loop`: index scan finds 211 bookings, then 211 index lookups into payments | 1.39 ms |

The planner switches join strategy by itself. A hash join is best when both sides are large.
A nested loop with an inner index lookup is best when the outer side is small, as it is here
(211 rows). **Index foreign-key columns used in joins.** PostgreSQL does not create these indexes
automatically.

## Q3: keep predicates sargable (no functions on indexed columns)

```sql
-- slow: the function hides the column from the index
SELECT count(*) FROM core.payments WHERE date(processed_at AT TIME ZONE 'UTC') = '2026-10-15';
-- fast: a half-open range on the raw column
SELECT count(*) FROM core.payments
WHERE processed_at >= '2026-10-15T00:00:00Z' AND processed_at < '2026-10-16T00:00:00Z';
```

| | Plan | Execution time |
|---|---|---|
| Function on column | `Index Only Scan` with **no `Index Cond`**: walks the whole index and filters every entry | 23.73 ms |
| Range predicate | `Index Only Scan` with `Index Cond` on the range | 0.19 ms |

Both plans "use the index", so look for the `Index Cond` line, not only the index name. A half-open range
(`>= start AND < next_day`) also avoids the `23:59:59.999` edge-case bug.

## Other decisions in this schema

| Decision | Where | Why |
|---|---|---|
| Stored generated column `booking_date` (IST business day) | `core.bookings` | Day-level reports filter and group on a plain column instead of computing `(requested_at AT TIME ZONE ...)::date` on every row. The composite index can then use it. |
| Partial index `WHERE driver_id IS NOT NULL` | `ix_bookings_driver` | About 7% of bookings have no driver. Leaving them out keeps the index smaller. |
| Partial index `WHERE status <> 'SUCCESS'` | `ops.pipeline_runs` | Monitoring screens ask for failures, which are a small fraction of runs. |
| `FILTER (WHERE ...)` aggregates | `pipelines/sql/transform/30_analytics.sql` | One pass over the table computes every status count. Separate subqueries per status would scan it repeatedly. |
| `NOT EXISTS` for anti-joins | `unpaid_completed_rides` | It states intent clearly and is planned as an anti-join. `NOT IN` returns no rows at all if the subquery yields a NULL. |
| Watermark + index on `_loaded_at` | `raw.*` | Incremental transforms read only new rows instead of rescanning the full raw history. |
| `ANALYZE` after bulk loads | loader / ops | Row estimates drive join choices. Stale statistics are the most common cause of a sudden "slow query". |
| Full refresh of small gold tables in one transaction | `30_analytics.sql` | Gold tables are small (about 17k rows in total). Rebuilding them is simpler than incremental merges, and `TRUNCATE` inside the transaction means readers never see a half-built table. |

## Checklist for a slow query

1. Run `EXPLAIN (ANALYZE, BUFFERS)` and find the node with the largest actual time.
2. Compare estimated and actual rows. Large gaps mean stale statistics (`ANALYZE`) or correlated predicates.
3. Check that filters on indexed columns appear as `Index Cond`, not as `Filter`.
4. Check join types: a `Nested Loop` over a large outer input is a red flag.
5. Look for sorts spilling to disk (`Sort Method: external merge`). Raise `work_mem` for the session, or add an index matching the `ORDER BY`.
