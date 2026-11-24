"""PostgreSQL catalog, stats, sampling and read-only query helpers (Phase 13).

Every statement here is fixed SQL over catalog views, or a query whose identifiers come from the
catalog and are quoted with psycopg.sql.Identifier (never string-concatenated user input). The
connection is the adapter's hardened read-only session.
"""

import datetime as dt
import decimal
import json
import time
import uuid
from typing import Any

import psycopg
from psycopg import sql

SYSTEM_SCHEMAS = ("pg_catalog", "information_schema", "pg_toast")
MAX_TABLES = 5000
KIND = {"r": "table", "p": "table", "v": "view", "m": "matview", "f": "external"}


def jsonable(value: Any) -> Any:
    """Driver values → JSON-safe values (Decimal, dates, UUID, bytes, ranges, arrays)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, decimal.Decimal):
        return float(value) if value.is_finite() else str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        return "\\x" + raw[:64].hex() + ("…" if len(raw) > 64 else "")
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    return str(value)


def list_tables(conn: psycopg.Connection) -> list[dict[str, Any]]:
    tables = conn.execute(
        """SELECT c.oid, n.nspname AS schema, c.relname AS name, c.relkind, obj_description(c.oid) AS comment
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f') AND NOT c.relispartition
              AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
              AND n.nspname NOT LIKE 'pg_temp%%' AND has_schema_privilege(n.oid, 'USAGE')
            ORDER BY 2, 3 LIMIT %s""",
        (MAX_TABLES,),
    ).fetchall()
    if not tables:
        return []
    oids = [t["oid"] for t in tables]
    columns = conn.execute(
        """SELECT a.attrelid AS oid, a.attname AS name, format_type(a.atttypid, a.atttypmod) AS data_type,
                  NOT a.attnotnull AS nullable, a.attnum AS ordinal,
                  pg_get_expr(d.adbin, d.adrelid) AS "default", col_description(a.attrelid, a.attnum) AS comment
             FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE a.attrelid = ANY(%s) AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY a.attrelid, a.attnum""",
        (oids,),
    ).fetchall()
    cons = conn.execute(
        """SELECT con.conrelid AS oid, con.contype, con.conname AS name,
                  ARRAY(SELECT a.attname FROM unnest(con.conkey) WITH ORDINALITY k(n, i)
                        JOIN pg_attribute a ON a.attrelid = con.conrelid AND a.attnum = k.n ORDER BY k.i) AS cols,
                  fn.nspname AS ref_schema, fc.relname AS ref_table,
                  ARRAY(SELECT a.attname FROM unnest(con.confkey) WITH ORDINALITY k(n, i)
                        JOIN pg_attribute a ON a.attrelid = con.confrelid AND a.attnum = k.n ORDER BY k.i) AS ref_cols
             FROM pg_constraint con
             LEFT JOIN pg_class fc ON fc.oid = con.confrelid LEFT JOIN pg_namespace fn ON fn.oid = fc.relnamespace
            WHERE con.conrelid = ANY(%s) AND con.contype IN ('p', 'f')""",
        (oids,),
    ).fetchall()
    indexes = conn.execute(
        """SELECT i.indrelid AS oid, ic.relname AS name, i.indisunique AS is_unique, i.indisprimary AS is_primary,
                  ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY k(n, o)
                        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = k.n ORDER BY k.o) AS cols,
                  pg_relation_size(ic.oid) AS bytes, coalesce(s.idx_scan, 0) AS scans,
                  pg_get_indexdef(i.indexrelid) AS definition
             FROM pg_index i JOIN pg_class ic ON ic.oid = i.indexrelid
             LEFT JOIN pg_stat_user_indexes s ON s.indexrelid = i.indexrelid
            WHERE i.indrelid = ANY(%s)""",
        (oids,),
    ).fetchall()

    by_oid: dict[int, dict[str, Any]] = {}
    for t in tables:
        by_oid[t["oid"]] = {"schema": t["schema"], "name": t["name"], "kind": KIND[t["relkind"]],
                            "comment": t["comment"], "columns": [], "indexes": [], "foreign_keys": [],
                            "primary_key": None}  # fmt: skip
    for c in columns:
        by_oid[c["oid"]]["columns"].append({k: c[k] for k in ("name", "data_type", "nullable", "ordinal", "default",
                                                               "comment")})  # fmt: skip
    for c in cons:
        if c["contype"] == "p":
            by_oid[c["oid"]]["primary_key"] = list(c["cols"])
        else:
            by_oid[c["oid"]]["foreign_keys"].append({"name": c["name"], "columns": list(c["cols"]),
                                                     "ref_table": f"{c['ref_schema']}.{c['ref_table']}",
                                                     "ref_columns": list(c["ref_cols"])})  # fmt: skip
    for i in indexes:
        by_oid[i["oid"]]["indexes"].append({"name": i["name"], "columns": list(i["cols"]), "unique": i["is_unique"],
                                            "primary": i["is_primary"], "bytes": i["bytes"], "scans": i["scans"],
                                            "definition": i["definition"]})  # fmt: skip
    for t in by_oid.values():
        pk = set(t["primary_key"] or [])
        for col in t["columns"]:
            col["is_pk"] = col["name"] in pk
    return list(by_oid.values())


def table_stats(conn: psycopg.Connection) -> list[dict[str, Any]]:
    return conn.execute(
        """SELECT n.nspname AS schema, c.relname AS name,
                  CASE WHEN c.reltuples < 0 THEN NULL ELSE c.reltuples::bigint END AS row_estimate,
                  pg_total_relation_size(c.oid) AS total_bytes, pg_relation_size(c.oid) AS table_bytes,
                  pg_indexes_size(c.oid) AS index_bytes,
                  s.n_dead_tup AS dead_rows, s.n_live_tup AS live_rows,
                  s.seq_scan AS seq_scans, s.idx_scan AS idx_scans,
                  s.n_tup_ins AS inserts, s.n_tup_upd AS updates, s.n_tup_del AS deletes,
                  greatest(s.last_vacuum, s.last_autovacuum) AS last_vacuum,
                  greatest(s.last_analyze, s.last_autoanalyze) AS last_analyze
             FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
             LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
            WHERE c.relkind IN ('r', 'p', 'm') AND NOT c.relispartition
              AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
              AND n.nspname NOT LIKE 'pg_temp%%' AND has_schema_privilege(n.oid, 'USAGE')
            LIMIT %s""",
        (MAX_TABLES,),
    ).fetchall()


def _ident(schema: str, table: str) -> sql.Composed:
    return sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))


def freshness_max(conn: psycopg.Connection, schema: str, table: str, column: str) -> Any:
    query = sql.SQL("SELECT max({}) AS m FROM {}").format(
        sql.Identifier(column), _ident(schema, table)
    )
    return conn.execute(query).fetchone()["m"]


def exact_count(conn: psycopg.Connection, schema: str, table: str) -> int:
    return conn.execute(
        sql.SQL("SELECT count(*) AS n FROM {}").format(_ident(schema, table))
    ).fetchone()["n"]


def sample(conn: psycopg.Connection, schema: str, table: str, columns: list[str], n: int,
           row_estimate: int | None) -> tuple[list[dict[str, Any]], str]:  # fmt: skip
    """FR-32: sampled rows. Small tables: LIMIT n. Large: TABLESAMPLE SYSTEM with a percentage sized
    to return ~n rows (block sampling, cheap); then a bounded LIMIT caps the result."""
    cols = sql.SQL(", ").join(sql.Identifier(c) for c in columns)
    if row_estimate is None or row_estimate <= n * 2:
        query = sql.SQL("SELECT {} FROM {} LIMIT {}").format(
            cols, _ident(schema, table), sql.Literal(n)
        )
        method = f"first {n} rows (table ≤ {n * 2} rows)"
    else:
        pct = min(100.0, max(0.0001, 100.0 * n * 1.5 / row_estimate))
        query = sql.SQL("SELECT {} FROM {} TABLESAMPLE SYSTEM ({}) LIMIT {}").format(
            cols, _ident(schema, table), sql.Literal(round(pct, 4)), sql.Literal(n))  # fmt: skip
        method = f"TABLESAMPLE SYSTEM ({pct:.4f}%)"
    rows = conn.execute(query).fetchall()
    if (
        not rows and row_estimate
    ):  # block sampling can miss on sparse tables; fall back to BERNOULLI
        pct = min(100.0, max(0.001, 100.0 * n * 2 / row_estimate))
        query = sql.SQL("SELECT {} FROM {} TABLESAMPLE BERNOULLI ({}) LIMIT {}").format(
            cols, _ident(schema, table), sql.Literal(round(pct, 4)), sql.Literal(n))  # fmt: skip
        rows = conn.execute(query).fetchall()
        method = f"TABLESAMPLE BERNOULLI ({pct:.4f}%)"
    return rows, method


def preview(
    conn: psycopg.Connection, schema: str, table: str, limit: int = 50
) -> tuple[list[str], list[list[Any]]]:
    cur = conn.execute(
        sql.SQL("SELECT * FROM {} LIMIT {}").format(_ident(schema, table), sql.Literal(limit))
    )
    names = [d.name for d in cur.description or []]
    return names, [[jsonable(r[n]) for n in names] for r in cur.fetchall()]


def run_readonly(conn: psycopg.Connection, statement: str, *, wrap: bool, max_rows: int, max_bytes: int,
                 timeout_s: int) -> dict[str, Any]:  # fmt: skip
    """Execute an already-guarded statement in a READ ONLY transaction that is always rolled back."""
    started = time.perf_counter()
    text = statement.strip().rstrip(";")
    query = f"SELECT * FROM ({text}) AS lens_q LIMIT {int(max_rows) + 1}" if wrap else text
    columns: list[dict[str, str]] = []
    rows: list[list[Any]] = []
    size = 0
    truncated = False
    with conn.transaction(force_rollback=True):
        conn.execute("SET TRANSACTION READ ONLY")
        conn.execute(
            sql.SQL("SET LOCAL statement_timeout = {}").format(sql.Literal(f"{int(timeout_s)}s"))
        )
        cur = conn.cursor(row_factory=psycopg.rows.tuple_row)
        cur.execute(query)  # noqa: S608 - statement passed the SQL guard; session is read-only
        if cur.description:
            columns = [
                {"name": d.name, "type": _type_name(conn, d.type_code)} for d in cur.description
            ]
            while True:
                batch = cur.fetchmany(500)
                if not batch:
                    break
                for raw in batch:
                    if len(rows) >= max_rows:
                        truncated = True
                        break
                    row = [jsonable(v) for v in raw]
                    size += len(json.dumps(row, default=str))
                    if size > max_bytes:
                        truncated = True
                        break
                    rows.append(row)
                if truncated:
                    break
    return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
            "elapsed_ms": int((time.perf_counter() - started) * 1000)}  # fmt: skip


_TYPE_CACHE: dict[int, str] = {}


def _type_name(conn: psycopg.Connection, oid: int) -> str:
    if oid not in _TYPE_CACHE:
        info = psycopg.adapters.types.get(oid)
        _TYPE_CACHE[oid] = info.name if info else str(oid)
    return _TYPE_CACHE[oid]


def explain(conn: psycopg.Connection, statement: str) -> Any:
    with conn.transaction(force_rollback=True):
        conn.execute("SET TRANSACTION READ ONLY")
        row = conn.execute(f"EXPLAIN (FORMAT JSON) {statement.strip().rstrip(';')}").fetchone()  # noqa: S608
    return next(iter(row.values()))
