"""SQLite adapter for uploaded database files (F-CON-1).

The file is opened `mode=ro&immutable=1` with `PRAGMA query_only = ON` (§18.5), so nothing can
write to it. There is no network, TLS or login: those stages report "not applicable". The path is
never user-supplied; the service resolves it from the workspace and upload id.
"""

import contextlib
import json
import os
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ClassVar

from rideops_api.lens.adapters.base import (
    AdapterError,
    Capability,
    DatabaseAdapter,
    Engine,
    StageResult,
    StatusSnapshot,
    Stopwatch,
    TestResult,
    now,
)
from rideops_api.lens.adapters.pg_catalog import jsonable

MAGIC = b"SQLite format 3\x00"


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def is_sqlite_file(path: str | Path) -> bool:
    with open(path, "rb") as fh:
        return fh.read(16) == MAGIC


class SQLiteAdapter(DatabaseAdapter):
    engine: ClassVar[Engine] = Engine.SQLITE
    default_port: ClassVar[int | None] = None
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.STATUS_BASIC, Capability.SCHEMA, Capability.TABLE_STATS, Capability.COLUMN_PROFILE,
        Capability.QUERY_CONSOLE, Capability.EXPLAIN,
    })  # fmt: skip

    @property
    def path(self) -> str:
        path = self.cfg.options.get("path")
        if not path:
            raise AdapterError("DB_SELECT", "CONN_DB_NOT_FOUND", "The uploaded file is missing.")
        return path

    @contextmanager
    def connect(self, timeout_s: int | None = None) -> Iterator[sqlite3.Connection]:
        path = self.path
        if not os.path.exists(path):
            raise AdapterError("DB_SELECT", "CONN_DB_NOT_FOUND", "The uploaded file is missing.")
        try:
            conn = sqlite3.connect(
                f"file:{path}?mode=ro&immutable=1", uri=True, check_same_thread=False
            )
            conn.execute("PRAGMA query_only = ON")
        except sqlite3.Error as exc:
            raise AdapterError(
                "DB_SELECT", "CONN_DB_NOT_FOUND", f"Not a readable SQLite database: {exc}"
            ) from exc
        conn.row_factory = sqlite3.Row
        deadline = time.monotonic() + (timeout_s or 30)
        conn.set_progress_handler(
            lambda: 1 if time.monotonic() > deadline else 0, 10_000
        )  # statement timeout
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _all(s, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        return [dict(r) for r in s.execute(sql, args).fetchall()]

    def test(self) -> TestResult:
        na = "not applicable (uploaded file)"
        stages = [
            StageResult("TCP", True, 0, na),
            StageResult("TLS", True, 0, na),
            StageResult("AUTH", True, 0, na),
        ]
        sw = Stopwatch()
        try:
            with self.connect() as s:
                version = s.execute("SELECT sqlite_version()").fetchone()[0]
                n = s.execute(
                    "SELECT count(*) FROM sqlite_master WHERE type IN ('table', 'view')"
                ).fetchone()[0]
                stages.append(StageResult("DB_SELECT", True, sw.ms, f"{n} tables and views"))
        except (AdapterError, sqlite3.Error) as exc:
            err = (
                exc
                if isinstance(exc, AdapterError)
                else AdapterError("DB_SELECT", "CONN_DB_NOT_FOUND", str(exc))
            )
            stages.append(StageResult("DB_SELECT", False, sw.ms, err.message, err.code))
            return TestResult(False, stages)
        stages += [StageResult("PRIVILEGES", True, 0, "Read-only by construction (immutable file)"),
                   StageResult("CAPABILITIES", True, 0, f"{len(self.capabilities)} capabilities")]  # fmt: skip
        return TestResult(True, stages, f"SQLite {version}", set(self.capabilities), [])

    def privilege_findings(self) -> list[dict[str, Any]]:
        return []

    def status(self) -> StatusSnapshot:
        sw = Stopwatch()
        try:
            with self.connect() as s:
                version = s.execute("SELECT sqlite_version()").fetchone()[0]
                pages = (
                    s.execute("PRAGMA page_count").fetchone()[0]
                    * s.execute("PRAGMA page_size").fetchone()[0]
                )
                latency = sw.ms
        except AdapterError as err:
            return StatusSnapshot(taken_at=now(), reachable=False, latency_ms=None, error_code=err.code,
                                  error_message=err.message, error_stage=err.stage)  # fmt: skip
        na = "not applicable to a SQLite file"
        return StatusSnapshot(taken_at=now(), reachable=True, latency_ms=latency, server_version=f"SQLite {version}",
                              db_size_bytes=pages, metrics={"latency_ms": latency, "db.size_bytes": pages},
                              unavailable={"connections": na, "replication": na, "cache.hit_ratio": na})  # fmt: skip

    # ------------------------------------------------------------------ analytics
    def list_tables(self, s) -> list[dict[str, Any]]:
        out = []
        for t in self._all(s, "SELECT name, type FROM sqlite_master WHERE type IN ('table', 'view') "
                              "AND name NOT LIKE 'sqlite_%' ORDER BY name"):  # fmt: skip
            cols = self._all(s, f"PRAGMA table_info({q(t['name'])})")
            pk = [c["name"] for c in sorted(cols, key=lambda c: c["pk"]) if c["pk"]]
            indexes = []
            for i in self._all(s, f"PRAGMA index_list({q(t['name'])})"):
                icols = [c["name"] for c in self._all(s, f"PRAGMA index_info({q(i['name'])})")]
                indexes.append({"name": i["name"], "columns": icols, "unique": bool(i["unique"]),
                                "primary": i["origin"] == "pk", "bytes": None, "scans": None,
                                "definition": f"{'UNIQUE ' if i['unique'] else ''}({','.join(icols)})"
                                + (" partial" if i.get("partial") else "")})  # fmt: skip
            fks: dict[int, dict[str, Any]] = {}
            for f in self._all(s, f"PRAGMA foreign_key_list({q(t['name'])})"):
                g = fks.setdefault(f["id"], {"name": f"fk_{t['name']}_{f['id']}", "columns": [], "ref_columns": [],
                                             "ref_table": f"main.{f['table']}"})  # fmt: skip
                g["columns"].append(f["from"])
                g["ref_columns"].append(f["to"])
            out.append({"schema": "main", "name": t["name"], "kind": t["type"], "comment": None,
                        "columns": [{"name": c["name"], "data_type": (c["type"] or "any").lower(),
                                     "nullable": not c["notnull"], "ordinal": c["cid"] + 1, "default": c["dflt_value"],
                                     "comment": None, "is_pk": bool(c["pk"])} for c in cols],
                        "indexes": indexes, "foreign_keys": list(fks.values()), "primary_key": pk or None})  # fmt: skip
        return out

    def table_stats(self, s) -> list[dict[str, Any]]:
        sizes: dict[str, int] = {}
        with contextlib.suppress(
            sqlite3.Error
        ):  # dbstat is compiled into most builds; sizes are optional
            sizes = {
                r["name"]: r["b"]
                for r in self._all(s, "SELECT name, SUM(pgsize) AS b FROM dbstat GROUP BY name")
            }
        out = []
        for t in self._all(
            s, "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ):
            n = s.execute(f"SELECT count(*) FROM {q(t['name'])}").fetchone()[
                0
            ]  # local file: exact counts are cheap
            out.append({"schema": "main", "name": t["name"], "row_estimate": n, "total_bytes": sizes.get(t["name"]),
                        "table_bytes": sizes.get(t["name"]), "index_bytes": None, "dead_rows": None, "live_rows": n,
                        "seq_scans": None, "idx_scans": None, "inserts": None, "updates": None, "deletes": None,
                        "last_vacuum": None, "last_analyze": None})  # fmt: skip
        return out

    def freshness_max(self, s, schema, table, column):
        return s.execute(f"SELECT max({q(column)}) FROM {q(table)}").fetchone()[0]

    def exact_count(self, s, schema, table) -> int:
        return int(s.execute(f"SELECT count(*) FROM {q(table)}").fetchone()[0])

    def sample(self, s, schema, table, columns, n, row_estimate):
        cols = ", ".join(q(c) for c in columns)
        if row_estimate is None or row_estimate <= n * 2:
            return self._all(
                s, f"SELECT {cols} FROM {q(table)} LIMIT ?", (n,)
            ), f"first {n} rows (small table)"
        rows = self._all(s, f"SELECT {cols} FROM {q(table)} WHERE rowid IN "
                            f"(SELECT rowid FROM {q(table)} ORDER BY random() LIMIT ?)", (n,))  # fmt: skip
        return rows, f"random {n} rows"

    def preview(self, s, schema, table, limit=50):
        cur = s.execute(f"SELECT * FROM {q(table)} LIMIT ?", (limit,))
        names = [d[0] for d in cur.description]
        return names, [[jsonable(v) for v in r] for r in cur.fetchall()]

    def run_readonly(self, s, statement, *, wrap, max_rows, max_bytes, timeout_s):
        started = time.perf_counter()
        deadline = time.monotonic() + timeout_s
        s.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        cur = s.execute(statement.strip().rstrip(";"))
        columns = [{"name": d[0], "type": ""} for d in (cur.description or [])]
        rows, size, truncated = [], 0, False
        for raw in cur:
            if len(rows) >= max_rows:
                truncated = True
                break
            row = [jsonable(v) for v in tuple(raw)]
            size += len(json.dumps(row, default=str))
            if size > max_bytes:
                truncated = True
                break
            rows.append(row)
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
                "elapsed_ms": int((time.perf_counter() - started) * 1000)}  # fmt: skip

    def explain(self, s, statement):
        return [
            dict(r)
            for r in s.execute(f"EXPLAIN QUERY PLAN {statement.strip().rstrip(';')}").fetchall()
        ]

    @staticmethod
    def readonly_grant_script(database: str, username: str) -> str:
        return "-- SQLite files are opened read-only and immutable; no grants are needed.\n"
