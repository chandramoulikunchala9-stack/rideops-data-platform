"""MySQL / MariaDB adapter (PyMySQL). Same contract as PostgresAdapter: staged test, read-only
session, status metrics, catalog/stats, sampling, guarded read-only queries."""

import contextlib
import json
import random
import re
import ssl
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, ClassVar

import pymysql
import pymysql.cursors

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

WRITE_PRIVS = {"INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "INDEX", "GRANT OPTION", "ALL PRIVILEGES",
               "CREATE VIEW", "TRIGGER", "EVENT", "CREATE ROUTINE", "ALTER ROUTINE", "EXECUTE", "FILE", "SUPER",
               "SHUTDOWN", "CREATE USER", "RELOAD"}  # fmt: skip
SYSTEM_DBS = ("mysql", "information_schema", "performance_schema", "sys")


def q(name: str) -> str:
    """Quote a MySQL identifier that came from the catalog."""
    return "`" + name.replace("`", "``") + "`"


def classify(exc: Exception) -> AdapterError:
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    msg = str(exc.args[1] if len(exc.args) > 1 else exc)
    low = msg.lower()
    if code in (1045, 1044, 1698) or "access denied" in low:
        return AdapterError("AUTH", "CONN_AUTH_FAILED", "Wrong username/password or auth method.")
    if code == 1049:
        return AdapterError(
            "DB_SELECT", "CONN_DB_NOT_FOUND", "Database doesn't exist or no access."
        )
    if code == 1040:
        return AdapterError("AUTH", "CONN_TOO_MANY", "The server has no free connections.")
    if "ssl" in low or "tls" in low or "certificate" in low:
        code_name = (
            "CONN_TLS_REQUIRED"
            if "required" in low or "secure transport" in low
            else "CONN_TLS_VERIFY_FAILED"
        )
        return AdapterError("TLS", code_name, msg)
    if code == 3159:  # insecure transport while the server requires secure transport
        return AdapterError("TLS", "CONN_TLS_REQUIRED", msg)
    if code in (2003, 2002) or "timed out" in low:
        return AdapterError("TCP", "CONN_TIMEOUT" if "timed out" in low else "CONN_REFUSED", msg)
    return AdapterError("AUTH", "CONN_ERROR", msg)


class MySQLAdapter(DatabaseAdapter):
    engine: ClassVar[Engine] = Engine.MYSQL
    default_port: ClassVar[int | None] = 3306
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.STATUS_BASIC, Capability.SESSIONS, Capability.LONG_QUERIES, Capability.LOCKS,
        Capability.CACHE_HIT, Capability.SCHEMA, Capability.TABLE_STATS, Capability.INDEX_USAGE,
        Capability.COLUMN_PROFILE, Capability.QUERY_CONSOLE, Capability.EXPLAIN, Capability.PRIVILEGE_CHECK,
    })  # fmt: skip

    # ----------------------------------------------------------------- connection
    def _ssl(self) -> ssl.SSLContext | None:
        mode = self.cfg.tls_mode
        if mode == "disable":
            return None
        ctx = ssl.create_default_context(cadata=self.secret.ca_cert_pem) if self.secret.ca_cert_pem else (
            ssl.create_default_context())  # fmt: skip
        if mode == "require":
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        elif mode == "verify-ca":
            ctx.check_hostname = False
        return ctx

    @contextmanager
    def connect(self) -> Iterator[pymysql.connections.Connection]:
        # verify-full checks the certificate against the host name, so it connects by name.
        host = (
            self.cfg.host
            if self.cfg.tls_mode == "verify-full" or not self.cfg.pinned_ip
            else self.cfg.pinned_ip
        )
        try:
            conn = pymysql.connect(host=host, port=self.cfg.port or 3306, user=self.cfg.username or "",
                                   password=self.secret.password or "", database=self.cfg.database or None,
                                   ssl=self._ssl(), connect_timeout=self.cfg.timeout_s, read_timeout=60,
                                   cursorclass=pymysql.cursors.DictCursor, autocommit=True, charset="utf8mb4",
                                   program_name="rideops-lens")  # fmt: skip
        except pymysql.MySQLError as exc:
            raise classify(exc) from exc
        try:
            with conn.cursor() as cur:  # session hardening, layer 2 (§18.5)
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                cur.execute("SET SESSION max_execution_time = 30000")
                cur.execute("SET SESSION innodb_lock_wait_timeout = 2")
            yield conn
        finally:
            conn.close()

    @staticmethod
    def _all(s, sql: str, args: Any = None) -> list[dict[str, Any]]:
        with s.cursor() as cur:
            cur.execute(sql, args)
            return list(cur.fetchall())

    def _one(self, s, sql: str, args: Any = None) -> dict[str, Any] | None:
        rows = self._all(s, sql, args)
        return rows[0] if rows else None

    def _db(self, s) -> str:
        return self._one(s, "SELECT DATABASE() AS db")["db"]

    # ----------------------------------------------------------------------- test
    def test(self) -> TestResult:
        stages = [tcp_stage(self.cfg, 3306)]
        if not stages[0].ok:
            return TestResult(False, stages)
        sw = Stopwatch()
        try:
            with self.connect() as s:
                ms = sw.ms
                cipher = self._one(s, "SHOW SESSION STATUS LIKE 'Ssl_cipher'")
                on = bool(cipher and cipher.get("Value"))
                stages.append(StageResult("TLS", True, 0, f"TLS on ({cipher['Value']})" if on else "TLS disabled (insecure)",
                                          warning=not on))  # fmt: skip
                stages.append(StageResult("AUTH", True, ms, f"Signed in as {self.cfg.username}"))
                sw = Stopwatch()
                v = self._one(s, "SELECT DATABASE() AS db, VERSION() AS v")
                stages.append(StageResult("DB_SELECT", True, sw.ms, f"Database {v['db']}"))
                version = ("MariaDB " if "mariadb" in v["v"].lower() else "MySQL ") + v["v"].split(
                    "-"
                )[0]
                sw = Stopwatch()
                findings = self._privilege_findings(s)
                write = sorted({f["privilege"] for f in findings if f["severity"] == "warning"})
                stages.append(StageResult("PRIVILEGES", not write, sw.ms,
                                          f"Role can write ({', '.join(write)}). Use a read-only user." if write
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
        findings = []
        for row in self._all(s, "SHOW GRANTS"):
            grant = next(iter(row.values()))
            m = re.match(r"GRANT (.+?) ON (\S+) TO", grant)
            if not m:
                continue
            privs = {p.strip().upper() for p in m.group(1).split(",")}
            target = m.group(2).replace("`", "")
            db_name = target.split(".")[0]
            if db_name in SYSTEM_DBS and db_name != "*":
                continue
            for p in sorted(privs & WRITE_PRIVS):
                findings.append({"privilege": p, "object": target, "severity": "warning"})
        if not any(f["privilege"] == "PROCESS" for f in findings):
            findings.append({"privilege": "PROCESS_MISSING", "object": "*", "severity": "info"})
        return findings

    def privilege_findings(self) -> list[dict[str, Any]]:
        with self.connect() as s:
            return self._privilege_findings(s)

    # --------------------------------------------------------------------- status
    def status(self) -> StatusSnapshot:
        sw = Stopwatch()
        try:
            with self.connect() as s:
                self._one(s, "SELECT 1")
                return self._collect(s, sw.ms)
        except AdapterError as err:
            return StatusSnapshot(taken_at=now(), reachable=False, latency_ms=None, error_code=err.code,
                                  error_message=err.message, error_stage=err.stage)  # fmt: skip

    def _collect(self, s, latency_ms: int) -> StatusSnapshot:
        st = {r["Variable_name"]: r["Value"] for r in self._all(s, "SHOW GLOBAL STATUS WHERE Variable_name IN "
              "('Uptime','Threads_connected','Threads_running','Innodb_buffer_pool_read_requests',"
              "'Innodb_buffer_pool_reads','Innodb_row_lock_current_waits','Com_commit','Com_rollback',"
              "'Slow_queries','Aborted_connects')")}  # fmt: skip
        max_conn = int(self._one(s, "SELECT @@max_connections AS m")["m"])
        size = self._one(s, "SELECT COALESCE(SUM(data_length + index_length), 0) AS b FROM information_schema.tables "
                            "WHERE table_schema = DATABASE()")["b"]  # fmt: skip
        version = self._one(s, "SELECT VERSION() AS v")["v"]
        unavailable: dict[str, str] = {}
        try:
            long_rows = self._all(s, "SELECT id AS id, user AS user, time AS time, info AS info FROM information_schema.processlist "
                                     "WHERE command <> 'Sleep' AND time > 300 ORDER BY time DESC LIMIT 10")  # fmt: skip
        except pymysql.MySQLError:
            long_rows = []
            unavailable["queries.long_running"] = "needs the PROCESS privilege"
        reads, req = (
            float(st.get("Innodb_buffer_pool_reads", 0)),
            float(st.get("Innodb_buffer_pool_read_requests", 0)),
        )
        total = int(st.get("Threads_connected", 0))
        metrics = {
            "latency_ms": latency_ms, "connections.total": total, "connections.active": int(st.get("Threads_running", 0)),
            "connections.idle": max(0, total - int(st.get("Threads_running", 0))), "connections.max": max_conn,
            "connections.used_pct": round(100.0 * total / max_conn, 1) if max_conn else None,
            "db.size_bytes": int(size), "uptime_s": int(st.get("Uptime", 0)),
            "queries.long_running": len(long_rows), "locks.blocked": int(st.get("Innodb_row_lock_current_waits", 0)),
            "cache.hit_ratio": round(1 - reads / req, 4) if req else None,
            "mysql.threads_running": int(st.get("Threads_running", 0)),
            "tx.commits.total": int(st.get("Com_commit", 0)), "tx.rollbacks.total": int(st.get("Com_rollback", 0)),
        }  # fmt: skip
        details = {"long_running": [{"pid": r["id"], "user": r["user"], "runtime_s": r["time"],
                                     "query": mask_query(r["info"])} for r in long_rows]}  # fmt: skip
        return StatusSnapshot(taken_at=now(), reachable=True, latency_ms=latency_ms,
                              server_version=("MariaDB " if "mariadb" in version.lower() else "MySQL ") + version.split("-")[0],
                              uptime_s=int(st.get("Uptime", 0)), db_size_bytes=int(size), metrics=metrics,
                              details=details, unavailable=unavailable)  # fmt: skip

    # ------------------------------------------------------------------ analytics
    def list_tables(self, s) -> list[dict[str, Any]]:
        db = self._db(s)
        tables = self._all(s, "SELECT table_name AS name, table_type AS table_type, table_comment AS table_comment "
                              "FROM information_schema.tables "
                              "WHERE table_schema = %s ORDER BY table_name LIMIT 5000", (db,))  # fmt: skip
        cols = self._all(s, "SELECT table_name AS table_name, column_name AS column_name, column_type AS column_type, "
                            "is_nullable AS is_nullable, ordinal_position AS ordinal_position, "
                            "column_default AS column_default, column_comment AS column_comment, "
                            "column_key AS column_key FROM information_schema.columns WHERE table_schema = %s "
                            "ORDER BY table_name, ordinal_position", (db,))  # fmt: skip
        idx = self._all(s, "SELECT table_name AS table_name, index_name AS index_name, non_unique AS non_unique, "
                           "column_name AS column_name, seq_in_index AS seq_in_index "
                           "FROM information_schema.statistics WHERE table_schema = %s ORDER BY table_name, "
                           "index_name, seq_in_index", (db,))  # fmt: skip
        fks = self._all(s, "SELECT table_name AS table_name, constraint_name AS constraint_name, column_name AS column_name, "
                           "referenced_table_schema AS referenced_table_schema, "
                           "referenced_table_name AS referenced_table_name, "
                           "referenced_column_name AS referenced_column_name FROM information_schema.key_column_usage "
                           "WHERE table_schema = %s AND referenced_table_name IS NOT NULL "
                           "ORDER BY constraint_name, ordinal_position", (db,))  # fmt: skip
        out: dict[str, dict[str, Any]] = {}
        for t in tables:
            out[t["name"]] = {"schema": db, "name": t["name"], "kind": "view" if t["table_type"] == "VIEW" else "table",
                              "comment": t["table_comment"] or None, "columns": [], "indexes": [], "foreign_keys": [],
                              "primary_key": None}  # fmt: skip
        for c in cols:
            if c["table_name"] in out:
                out[c["table_name"]]["columns"].append({
                    "name": c["column_name"], "data_type": c["column_type"], "nullable": c["is_nullable"] == "YES",
                    "ordinal": c["ordinal_position"], "default": c["column_default"], "comment": c["column_comment"] or None,
                    "is_pk": c["column_key"] == "PRI"})  # fmt: skip
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for i in idx:
            g = grouped.setdefault((i["table_name"], i["index_name"]), {"name": i["index_name"], "columns": [],
                                   "unique": not i["non_unique"], "primary": i["index_name"] == "PRIMARY",
                                   "bytes": None, "scans": None, "definition": None})  # fmt: skip
            g["columns"].append(i["column_name"])
        for (table, _), g in grouped.items():
            if table in out:
                g["definition"] = f"{'UNIQUE ' if g['unique'] else ''}({','.join(g['columns'])})"
                out[table]["indexes"].append(g)
                if g["primary"]:
                    out[table]["primary_key"] = g["columns"]
        fk_grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for f in fks:
            g = fk_grouped.setdefault((f["table_name"], f["constraint_name"]), {
                "name": f["constraint_name"], "columns": [], "ref_columns": [],
                "ref_table": f"{f['referenced_table_schema']}.{f['referenced_table_name']}"})  # fmt: skip
            g["columns"].append(f["column_name"])
            g["ref_columns"].append(f["referenced_column_name"])
        for (table, _), g in fk_grouped.items():
            if table in out:
                out[table]["foreign_keys"].append(g)
        return list(out.values())

    def table_stats(self, s) -> list[dict[str, Any]]:
        rows = self._all(s, "SELECT table_schema AS `schema`, table_name AS name, table_rows AS row_estimate, "
                            "data_length + index_length AS total_bytes, data_length AS table_bytes, "
                            "index_length AS index_bytes, update_time AS update_time FROM information_schema.tables "
                            "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE'")  # fmt: skip
        return [{**r, "dead_rows": None, "live_rows": r["row_estimate"], "seq_scans": None, "idx_scans": None,
                 "inserts": None, "updates": None, "deletes": None, "last_vacuum": None,
                 "last_analyze": r.pop("update_time")} for r in rows]  # fmt: skip

    def freshness_max(self, s, schema, table, column):
        return self._one(s, f"SELECT MAX({q(column)}) AS m FROM {q(schema)}.{q(table)}")["m"]

    def exact_count(self, s, schema, table) -> int:
        return int(self._one(s, f"SELECT COUNT(*) AS n FROM {q(schema)}.{q(table)}")["n"])

    def sample(self, s, schema, table, columns, n, row_estimate):
        """Primary-key range sampling (§14.4): a random start on an integer PK, never ORDER BY RAND()."""
        cols = ", ".join(q(c) for c in columns)
        src = f"{q(schema)}.{q(table)}"
        if row_estimate is None or row_estimate <= n * 2:
            return self._all(
                s, f"SELECT {cols} FROM {src} LIMIT %s", (n,)
            ), f"first {n} rows (small table)"
        pk = self._all(s, "SELECT column_name AS column_name, data_type AS data_type FROM information_schema.columns "
                          "WHERE table_schema = %s "
                          "AND table_name = %s AND column_key = 'PRI'", (schema, table))  # fmt: skip
        if len(pk) == 1 and "int" in pk[0]["data_type"]:
            col = q(pk[0]["column_name"])
            bounds = self._one(s, f"SELECT MIN({col}) AS lo, MAX({col}) AS hi FROM {src}")
            start = random.randint(int(bounds["lo"]), max(int(bounds["lo"]), int(bounds["hi"]) - n))
            rows = self._all(
                s, f"SELECT {cols} FROM {src} WHERE {col} >= %s ORDER BY {col} LIMIT %s", (start, n)
            )
            return rows, f"primary-key range from {pk[0]['column_name']} ≥ {start}"
        return self._all(
            s, f"SELECT {cols} FROM {src} LIMIT %s", (n,)
        ), f"first {n} rows (no integer key)"

    def preview(self, s, schema, table, limit=50):
        with s.cursor(pymysql.cursors.Cursor) as cur:
            cur.execute(f"SELECT * FROM {q(schema)}.{q(table)} LIMIT %s", (limit,))
            names = [d[0] for d in cur.description]
            return names, [[jsonable(v) for v in r] for r in cur.fetchall()]

    def run_readonly(self, s, statement, *, wrap, max_rows, max_bytes, timeout_s):
        started = time.perf_counter()
        rows: list[list[Any]] = []
        size, truncated = 0, False
        with s.cursor(pymysql.cursors.SSCursor) as cur:  # unbuffered: stop reading at the cap
            cur.execute(f"SET SESSION max_execution_time = {int(timeout_s) * 1000}")
            cur.execute("START TRANSACTION READ ONLY")
            try:
                cur.execute(statement.strip().rstrip(";"))
                columns = [{"name": d[0], "type": str(d[1])} for d in (cur.description or [])]
                for raw in cur:
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
                if getattr(cur, "_result", None) is not None:
                    with contextlib.suppress(pymysql.MySQLError, AttributeError):
                        cur.fetchall()  # drain the unbuffered result before ROLLBACK
                cur.execute("ROLLBACK")
        return {"columns": columns, "rows": rows, "row_count": len(rows), "truncated": truncated,
                "elapsed_ms": int((time.perf_counter() - started) * 1000)}  # fmt: skip

    def explain(self, s, statement):
        row = self._one(s, f"EXPLAIN FORMAT=JSON {statement.strip().rstrip(';')}")
        return json.loads(next(iter(row.values())))

    @staticmethod
    def readonly_grant_script(database: str, username: str) -> str:
        db = q(database or "your_database")
        user = "'" + (username or "lens_ro").replace("'", "''") + "'"
        return f"""-- Read-only user for RideOps Lens (MySQL / MariaDB)
CREATE USER {user}@'%' IDENTIFIED BY 'choose-a-strong-password' REQUIRE SSL WITH MAX_USER_CONNECTIONS 5;
GRANT SELECT, SHOW VIEW ON {db}.* TO {user}@'%';
GRANT PROCESS ON *.* TO {user}@'%';                     -- optional: long-running queries
GRANT SELECT ON performance_schema.* TO {user}@'%';    -- optional
"""
