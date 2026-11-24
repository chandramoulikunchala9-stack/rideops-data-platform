"""SQL Server / Azure SQL adapter (pymssql, bundled FreeTDS).

Deviation from §7.1 (pyodbc + MS ODBC Driver 18): pymssql needs no system ODBC packages, so it
installs anywhere. Limitation: it can *require* TLS but cannot verify the server certificate, so
verify-ca / verify-full encrypt without verification and the TLS stage says so (warning).
Layer-2 read-only: SQL Server has no read-only session for primaries, so every user statement runs
inside BEGIN TRAN … ROLLBACK with a lock timeout; the SQL guard (layer 3) blocks writes first.
"""

import json
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

import pymssql

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
    tcp_stage,
)
from rideops_api.lens.adapters.pg_catalog import jsonable
from rideops_api.lens.adapters.postgres import mask_query

WRITE_PERMS = {"INSERT", "UPDATE", "DELETE", "ALTER", "CONTROL", "CREATE TABLE", "CREATE VIEW", "CREATE PROCEDURE",
               "ALTER ANY SCHEMA", "TAKE OWNERSHIP", "EXECUTE", "IMPERSONATE", "REFERENCES"}  # fmt: skip
SYSTEM_SCHEMAS = ("sys", "INFORMATION_SCHEMA", "guest", "db_owner", "db_accessadmin", "db_securityadmin",
                  "db_ddladmin", "db_backupoperator", "db_datareader", "db_datawriter", "db_denydatareader",
                  "db_denydatawriter")  # fmt: skip


def q(name: str) -> str:
    return "[" + name.replace("]", "]]") + "]"


def classify(exc: Exception) -> AdapterError:
    msg = str(exc)
    low = msg.lower()
    if (
        "4060" in msg or "cannot open database" in low
    ):  # SQL Server also adds 18456, so check this first
        return AdapterError(
            "DB_SELECT", "CONN_DB_NOT_FOUND", "Database doesn't exist or no access."
        )
    if "18456" in msg or "login failed" in low:
        return AdapterError("AUTH", "CONN_AUTH_FAILED", "Wrong username/password or auth method.")
    if "ssl" in low or "tls" in low or "encrypt" in low:
        return AdapterError(
            "TLS", "CONN_TLS_REQUIRED", "TLS negotiation failed: " + msg.splitlines()[0][:200]
        )
    if "timed out" in low or "timeout" in low:
        return AdapterError(
            "TCP", "CONN_TIMEOUT", "Timed out. Is the database public and is our IP allowlisted?"
        )
    if "unable to connect" in low or "connection refused" in low:
        return AdapterError("TCP", "CONN_REFUSED", "Connection refused on that port.")
    return AdapterError(
        "AUTH", "CONN_ERROR", msg.splitlines()[0][:300] if msg else type(exc).__name__
    )


class MSSQLAdapter(DatabaseAdapter):
    engine: ClassVar[Engine] = Engine.MSSQL
    default_port: ClassVar[int | None] = 1433
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.STATUS_BASIC, Capability.SESSIONS, Capability.LONG_QUERIES, Capability.LOCKS,
        Capability.CACHE_HIT, Capability.SCHEMA, Capability.TABLE_STATS, Capability.INDEX_USAGE,
        Capability.COLUMN_PROFILE, Capability.QUERY_CONSOLE, Capability.EXPLAIN, Capability.PRIVILEGE_CHECK,
    })  # fmt: skip

    @contextmanager
    def connect(self) -> Iterator[Any]:
        try:
            conn = pymssql.connect(
                server=self.cfg.pinned_ip or self.cfg.host, port=self.cfg.port or 1433,
                user=self.cfg.username or "", password=self.secret.password or "",
                database=self.cfg.database or "master", login_timeout=self.cfg.timeout_s, timeout=30,
                appname="rideops-lens", as_dict=True, autocommit=True,
                encryption="off" if self.cfg.tls_mode == "disable" else "require",
                conn_properties=["SET LOCK_TIMEOUT 2000", "SET TRANSACTION ISOLATION LEVEL READ COMMITTED",
                                 "SET ARITHABORT ON"],
            )  # fmt: skip
        except pymssql.Error as exc:
            err = classify(exc)
            if (
                err.code == "CONN_AUTH_FAILED"
                and (self.cfg.database or "master").lower() != "master"
            ):
                # FreeTDS reports an unknown database as a login failure (it drops error 4060): if the same
                # credentials work against master, the database name is the problem, not the password.
                try:
                    pymssql.connect(
                        server=self.cfg.pinned_ip or self.cfg.host,
                        port=self.cfg.port or 1433,
                        user=self.cfg.username or "",
                        password=self.secret.password or "",
                        database="master",
                        login_timeout=self.cfg.timeout_s,
                        encryption="off" if self.cfg.tls_mode == "disable" else "require",
                    ).close()
                    err = AdapterError(
                        "DB_SELECT", "CONN_DB_NOT_FOUND", "Database doesn't exist or no access."
                    )
                except pymssql.Error:
                    pass
            raise err from exc
        try:
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _all(s, sql: str, args: Any = None) -> list[dict[str, Any]]:
        cur = s.cursor(as_dict=True)
        cur.execute(sql, args) if args is not None else cur.execute(sql)
        return list(cur.fetchall())

    def _one(self, s, sql: str, args: Any = None) -> dict[str, Any] | None:
        rows = self._all(s, sql, args)
        return rows[0] if rows else None

    # ----------------------------------------------------------------------- test
    def test(self) -> TestResult:
        stages = [tcp_stage(self.cfg, 1433)]
        if not stages[0].ok:
            return TestResult(False, stages)
        sw = Stopwatch()
        try:
            with self.connect() as s:
                ms = sw.ms
                enc = self._one(
                    s,
                    "SELECT encrypt_option AS e FROM sys.dm_exec_connections WHERE session_id = @@SPID",
                )
                on = bool(enc and str(enc["e"]).upper() == "TRUE")
                if not on:
                    tls = StageResult("TLS", True, 0, "TLS disabled (insecure)", warning=True)
                elif self.cfg.tls_mode in ("verify-ca", "verify-full"):
                    tls = StageResult("TLS", True, 0, "Encrypted, but this driver can't verify the server certificate",
                                      "TLS_NOT_VERIFIED", warning=True)  # fmt: skip
                else:
                    tls = StageResult("TLS", True, 0, "TLS on (require)")
                stages += [tls, StageResult("AUTH", True, ms, f"Signed in as {self.cfg.username}")]
                sw = Stopwatch()
                v = self._one(s, "SELECT DB_NAME() AS db, CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)) AS v, "
                                 "CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS ed")  # fmt: skip
                stages.append(StageResult("DB_SELECT", True, sw.ms, f"Database {v['db']}"))
                version = f"SQL Server {v['v']}"
                sw = Stopwatch()
                findings = self._privilege_findings(s)
                write = sorted({f["privilege"] for f in findings if f["severity"] == "warning"})
                stages.append(StageResult("PRIVILEGES", not write, sw.ms,
                                          f"Login can write ({', '.join(write[:5])}). Use a read-only user." if write
                                          else "Read-only user", "PRIV_WRITE_GRANTED" if write else None,
                                          warning=bool(write)))  # fmt: skip
                stages.append(
                    StageResult("CAPABILITIES", True, 0, f"{len(self.capabilities)} capabilities")
                )
                return TestResult(True, stages, version, set(self.capabilities), findings)
        except AdapterError as err:
            if err.stage == "TLS":
                stages.append(StageResult("TLS", False, sw.ms, err.message, err.code))
            elif err.stage == "DB_SELECT":
                stages += [StageResult("TLS", True, 0, ""), StageResult("AUTH", True, sw.ms, ""),
                           StageResult("DB_SELECT", False, 0, err.message, err.code)]  # fmt: skip
            else:
                stages += [
                    StageResult("TLS", True, 0, ""),
                    StageResult("AUTH", False, sw.ms, err.message, err.code),
                ]
            return TestResult(False, stages)

    def _privilege_findings(self, s) -> list[dict[str, Any]]:
        if self._one(s, "SELECT IS_SRVROLEMEMBER('sysadmin') AS a")["a"] == 1:
            return [{"privilege": "SYSADMIN", "object": "*", "severity": "warning"}]
        perms = {
            r["permission_name"]
            for r in self._all(s, "SELECT permission_name FROM fn_my_permissions(NULL, 'DATABASE')")
        }
        findings = [
            {"privilege": p, "object": "database", "severity": "warning"}
            for p in sorted(perms & WRITE_PERMS)
        ]
        if "VIEW DATABASE STATE" not in perms:
            findings.append(
                {"privilege": "VIEW_DATABASE_STATE_MISSING", "object": "*", "severity": "info"}
            )
        return findings

    def privilege_findings(self) -> list[dict[str, Any]]:
        with self.connect() as s:
            return self._privilege_findings(s)

    # --------------------------------------------------------------------- status
    def status(self) -> StatusSnapshot:
        sw = Stopwatch()
        try:
            with self.connect() as s:
                self._one(s, "SELECT 1 AS one")
                return self._collect(s, sw.ms)
        except AdapterError as err:
            return StatusSnapshot(taken_at=now(), reachable=False, latency_ms=None, error_code=err.code,
                                  error_message=err.message, error_stage=err.stage)  # fmt: skip

    def _try(
        self, s, sql: str, unavailable: dict[str, str], key: str, reason: str
    ) -> list[dict[str, Any]] | None:
        try:
            return self._all(s, sql)
        except pymssql.Error:
            unavailable[key] = reason
            return None

    def _collect(self, s, latency_ms: int) -> StatusSnapshot:
        unavailable: dict[str, str] = {}
        need = "needs VIEW SERVER STATE"
        version = self._one(
            s, "SELECT CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(64)) AS v"
        )["v"]
        size = self._one(
            s,
            "SELECT CAST(SUM(CAST(size AS bigint)) * 8192 AS bigint) AS b FROM sys.database_files",
        )["b"]
        max_conn = self._one(s, "SELECT @@MAX_CONNECTIONS AS m")["m"]
        sessions = self._try(s, "SELECT COUNT(*) AS total, SUM(CASE WHEN status = 'running' THEN 1 ELSE 0 END) AS active "
                                "FROM sys.dm_exec_sessions WHERE is_user_process = 1", unavailable,
                             "connections.total", need)  # fmt: skip
        uptime = self._try(s, "SELECT DATEDIFF(second, sqlserver_start_time, SYSDATETIME()) AS u FROM sys.dm_os_sys_info",
                           unavailable, "uptime_s", need)  # fmt: skip
        long_rows = self._try(s, "SELECT TOP 10 r.session_id AS pid, s.login_name AS usr, r.total_elapsed_time / 1000 AS secs, "
                                 "t.text AS q FROM sys.dm_exec_requests r JOIN sys.dm_exec_sessions s ON s.session_id = "
                                 "r.session_id OUTER APPLY sys.dm_exec_sql_text(r.sql_handle) t "
                                 "WHERE r.total_elapsed_time > 300000 AND r.session_id <> @@SPID", unavailable,
                              "queries.long_running", need) or []  # fmt: skip
        blocked = self._try(s, "SELECT COUNT(*) AS n FROM sys.dm_exec_requests WHERE blocking_session_id > 0",
                            unavailable, "locks.blocked", need)  # fmt: skip
        hit = self._try(s, "SELECT a.cntr_value * 1.0 / NULLIF(b.cntr_value, 0) AS r FROM sys.dm_os_performance_counters a "
                           "JOIN sys.dm_os_performance_counters b ON b.object_name = a.object_name "
                           "WHERE a.counter_name = 'Buffer cache hit ratio' AND b.counter_name = 'Buffer cache hit ratio base'",
                        unavailable, "cache.hit_ratio", need)  # fmt: skip
        total = sessions[0]["total"] if sessions else None
        metrics = {
            "latency_ms": latency_ms, "connections.total": total,
            "connections.active": sessions[0]["active"] if sessions else None, "connections.max": max_conn,
            "connections.used_pct": round(100.0 * total / max_conn, 2) if total is not None and max_conn else None,
            "db.size_bytes": size, "uptime_s": uptime[0]["u"] if uptime else None,
            "queries.long_running": len(long_rows), "locks.blocked": blocked[0]["n"] if blocked else None,
            "cache.hit_ratio": round(float(hit[0]["r"]), 4) if hit and hit[0]["r"] is not None else None,
        }  # fmt: skip
        details = {"long_running": [{"pid": r["pid"], "user": r["usr"], "runtime_s": r["secs"],
                                     "query": mask_query(r["q"])} for r in long_rows]}  # fmt: skip
        return StatusSnapshot(taken_at=now(), reachable=True, latency_ms=latency_ms, server_version=f"SQL Server {version}",
                              uptime_s=metrics["uptime_s"], db_size_bytes=size, metrics=metrics, details=details,
                              unavailable=unavailable)  # fmt: skip

    # ------------------------------------------------------------------ analytics
    def list_tables(self, s) -> list[dict[str, Any]]:
        objs = self._all(s, "SELECT o.object_id AS oid, sc.name AS sch, o.name AS name, o.type AS t FROM sys.objects o "
                            "JOIN sys.schemas sc ON sc.schema_id = o.schema_id WHERE o.type IN ('U', 'V') "
                            "AND o.is_ms_shipped = 0 ORDER BY sc.name, o.name")  # fmt: skip
        out = {o["oid"]: {"schema": o["sch"], "name": o["name"], "kind": "table" if o["t"].strip() == "U" else "view",
                          "comment": None, "columns": [], "indexes": [], "foreign_keys": [], "primary_key": None}
               for o in objs if o["sch"] not in SYSTEM_SCHEMAS}  # fmt: skip
        for c in self._all(
            s,
            "SELECT c.object_id AS oid, c.name AS name, TYPE_NAME(c.user_type_id) AS typ, c.max_length AS len, "
            "c.is_nullable AS nullable, c.column_id AS ord FROM sys.columns c ORDER BY c.object_id, c.column_id",
        ):
            if c["oid"] in out:
                out[c["oid"]]["columns"].append(
                    {
                        "name": c["name"],
                        "data_type": c["typ"],
                        "nullable": bool(c["nullable"]),
                        "ordinal": c["ord"],
                        "default": None,
                        "comment": None,
                        "is_pk": False,
                    }
                )
        idx: dict[tuple, dict[str, Any]] = {}
        for i in self._all(s, "SELECT i.object_id AS oid, i.name AS name, i.is_unique AS u, i.is_primary_key AS pk, "
                              "COL_NAME(ic.object_id, ic.column_id) AS col, ic.key_ordinal AS ko, "
                              "ISNULL(us.user_seeks + us.user_scans + us.user_lookups, 0) AS scans "
                              "FROM sys.indexes i JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id "
                              "LEFT JOIN sys.dm_db_index_usage_stats us ON us.object_id = i.object_id AND us.index_id = i.index_id "
                              "AND us.database_id = DB_ID() WHERE i.name IS NOT NULL AND ic.key_ordinal > 0 "
                              "ORDER BY i.object_id, i.name, ic.key_ordinal"):  # fmt: skip
            if i["oid"] not in out:
                continue
            g = idx.setdefault(
                (i["oid"], i["name"]),
                {
                    "name": i["name"],
                    "columns": [],
                    "unique": bool(i["u"]),
                    "primary": bool(i["pk"]),
                    "bytes": None,
                    "scans": i["scans"],
                },
            )
            g["columns"].append(i["col"])
        for (oid, _), g in idx.items():
            g["definition"] = f"{'UNIQUE ' if g['unique'] else ''}({','.join(g['columns'])})"
            out[oid]["indexes"].append(g)
            if g["primary"]:
                out[oid]["primary_key"] = g["columns"]
        fks: dict[tuple, dict[str, Any]] = {}
        for f in self._all(
            s,
            "SELECT fk.parent_object_id AS oid, fk.name AS name, COL_NAME(fkc.parent_object_id, "
            "fkc.parent_column_id) AS col, OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS rs, "
            "OBJECT_NAME(fk.referenced_object_id) AS rt, COL_NAME(fkc.referenced_object_id, "
            "fkc.referenced_column_id) AS rc FROM sys.foreign_keys fk JOIN sys.foreign_key_columns fkc "
            "ON fkc.constraint_object_id = fk.object_id ORDER BY fk.name, fkc.constraint_column_id",
        ):
            if f["oid"] in out:
                g = fks.setdefault((f["oid"], f["name"]), {"name": f["name"], "columns": [], "ref_columns": [],
                                                          "ref_table": f"{f['rs']}.{f['rt']}"})  # fmt: skip
                g["columns"].append(f["col"])
                g["ref_columns"].append(f["rc"])
        for (oid, _), g in fks.items():
            out[oid]["foreign_keys"].append(g)
        for t in out.values():
            pk = set(t["primary_key"] or [])
            for c in t["columns"]:
                c["is_pk"] = c["name"] in pk
        return list(out.values())

    def table_stats(self, s) -> list[dict[str, Any]]:
        rows = self._all(s, "SELECT OBJECT_SCHEMA_NAME(p.object_id) AS sch, OBJECT_NAME(p.object_id) AS name, "
                            "SUM(CASE WHEN p.index_id IN (0, 1) THEN p.row_count ELSE 0 END) AS row_estimate, "
                            "SUM(p.used_page_count) * 8192 AS total_bytes, "
                            "SUM(CASE WHEN p.index_id IN (0, 1) THEN p.used_page_count ELSE 0 END) * 8192 AS table_bytes "
                            "FROM sys.dm_db_partition_stats p JOIN sys.objects o ON o.object_id = p.object_id "
                            "WHERE o.type = 'U' AND o.is_ms_shipped = 0 GROUP BY p.object_id")  # fmt: skip
        return [
            {
                "schema": r["sch"],
                "name": r["name"],
                "row_estimate": r["row_estimate"],
                "total_bytes": r["total_bytes"],
                "table_bytes": r["table_bytes"],
                "index_bytes": (r["total_bytes"] or 0) - (r["table_bytes"] or 0),
                "dead_rows": None,
                "live_rows": r["row_estimate"],
                "seq_scans": None,
                "idx_scans": None,
                "inserts": None,
                "updates": None,
                "deletes": None,
                "last_vacuum": None,
                "last_analyze": None,
            }
            for r in rows
        ]

    def freshness_max(self, s, schema, table, column):
        return self._one(s, f"SELECT MAX({q(column)}) AS m FROM {q(schema)}.{q(table)}")["m"]

    def exact_count(self, s, schema, table) -> int:
        return int(self._one(s, f"SELECT COUNT_BIG(*) AS n FROM {q(schema)}.{q(table)}")["n"])

    def sample(self, s, schema, table, columns, n, row_estimate):
        cols = ", ".join(q(c) for c in columns)
        src = f"{q(schema)}.{q(table)}"
        # TABLESAMPLE picks whole pages, so it is unreliable on small tables: read rows directly there.
        if row_estimate is None or row_estimate <= max(n * 2, 100_000):
            return self._all(s, f"SELECT TOP ({int(n)}) {cols} FROM {src}"), f"first {n} rows"
        rows = self._all(
            s, f"SELECT TOP ({int(n)}) {cols} FROM {src} TABLESAMPLE ({int(n * 2)} ROWS)"
        )
        if not rows:
            return self._all(
                s, f"SELECT TOP ({int(n)}) {cols} FROM {src}"
            ), f"first {n} rows (sample was empty)"
        return rows, f"TABLESAMPLE ({n * 2} ROWS)"

    def preview(self, s, schema, table, limit=50):
        cur = s.cursor(as_dict=False)
        cur.execute(f"SELECT TOP ({int(limit)}) * FROM {q(schema)}.{q(table)}")
        names = [d[0] for d in cur.description]
        return names, [[jsonable(v) for v in r] for r in cur.fetchall()]

    def run_readonly(self, s, statement, *, wrap, max_rows, max_bytes, timeout_s):
        started = time.perf_counter()
        rows: list[list[Any]] = []
        size, truncated, columns = 0, False, []
        cur = s.cursor(as_dict=False)
        cur.execute("BEGIN TRANSACTION")
        try:
            cur.execute(statement.strip().rstrip(";"))
            columns = [{"name": d[0], "type": str(d[1])} for d in (cur.description or [])]
            while columns:
                raw = cur.fetchone()
                if raw is None:
                    break
                if len(rows) >= max_rows:
                    truncated = True
                    break
                row = [jsonable(v) for v in raw]
                size += len(json.dumps(row, default=str))
                if size > max_bytes:
                    truncated = True
                    break
                rows.append(row)
        finally:
            try:
                while cur.nextset():  # discard what we didn't read
                    pass
            except pymssql.Error:
                pass
            cur.execute("IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION")
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
                "elapsed_ms": int((time.perf_counter() - started) * 1000)}  # fmt: skip

    def explain(self, s, statement):
        cur = s.cursor(as_dict=False)
        cur.execute("SET SHOWPLAN_XML ON")
        try:
            cur.execute(statement.strip().rstrip(";"))
            xml = cur.fetchone()[0]
        finally:
            cur.execute("SET SHOWPLAN_XML OFF")
        return {"format": "showplan_xml", "plan": xml[:200_000]}

    @staticmethod
    def readonly_grant_script(database: str, username: str) -> str:
        db, user = q(database or "your_database"), q(username or "lens_ro")
        return f"""-- Read-only login for RideOps Lens (SQL Server / Azure SQL)
CREATE LOGIN {user} WITH PASSWORD = 'choose-a-strong-password';
USE {db};
CREATE USER {user} FOR LOGIN {user};
ALTER ROLE db_datareader ADD MEMBER {user};
GRANT VIEW DATABASE STATE TO {user};      -- DMVs for status metrics
-- GRANT VIEW SERVER STATE TO {user};     -- optional: server-wide metrics (run in master)
"""
