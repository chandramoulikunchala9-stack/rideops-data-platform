"""MongoDB / Atlas adapter (pymongo).

Collections play the part of tables; their "columns" are inferred from a sample of documents.
The query console takes JSON instead of SQL (validated by `mongo_guard`):
    {"collection": "orders", "find": {"status": "completed"}, "projection": {...}, "sort": {...}, "limit": 100}
    {"collection": "orders", "aggregate": [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]}
Read-only (§18.5): secondaryPreferred reads, maxTimeMS on every command, and only find / aggregate /
count / listCollections / stats commands are ever issued; $out, $merge, $function, $where and
$accumulator are rejected before anything reaches the server.
mongodb+srv: hosts come from DNS SRV records, so the Network Guard checks every resolved host; the
driver then connects by name (no IP pinning for SRV, a documented limitation).
"""

import json
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import Any, ClassVar

import pymongo
from bson import ObjectId
from pymongo import errors as merr

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

WRITE_ACTIONS = {"insert", "update", "remove", "dropCollection", "dropDatabase", "createCollection", "createIndex",
                 "dropIndex", "renameCollectionSameDB", "collMod", "convertToCapped", "emptycapped",
                 "bypassDocumentValidation", "createUser", "dropUser", "grantRole", "anyAction"}  # fmt: skip
INFER_SAMPLE = 200
MAX_TIME_MS = 30_000


def jsonable_doc(v: Any) -> Any:
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, dict):
        return {k: jsonable_doc(x) for k, x in v.items()}
    if isinstance(v, list):
        return [jsonable_doc(x) for x in v]
    return jsonable(v)


def bson_type(v: Any) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "double"
    if isinstance(v, str):
        return "string"
    if isinstance(v, datetime):
        return "date"
    if isinstance(v, ObjectId):
        return "objectId"
    if isinstance(v, list):
        return "array"
    if isinstance(v, dict):
        return "object"
    return type(v).__name__.lower()


def classify(exc: Exception) -> AdapterError:
    msg = str(exc)
    low = msg.lower()
    if isinstance(exc, merr.OperationFailure) and (exc.code in (18, 8000) or "auth" in low):
        return AdapterError("AUTH", "CONN_AUTH_FAILED", "Wrong username/password or auth source.")
    if "ssl" in low or "tls" in low or "certificate" in low:
        return AdapterError("TLS", "CONN_TLS_VERIFY_FAILED", msg.splitlines()[0][:300])
    if isinstance(exc, (merr.ServerSelectionTimeoutError, merr.NetworkTimeout)):
        return AdapterError(
            "TCP", "CONN_TIMEOUT", "Timed out. Is the cluster public and is our IP allowlisted?"
        )
    if isinstance(exc, merr.ConnectionFailure):
        return AdapterError("TCP", "CONN_REFUSED", msg.splitlines()[0][:300])
    return AdapterError("AUTH", "CONN_ERROR", msg.splitlines()[0][:300])


class MongoSession:
    def __init__(self, client: pymongo.MongoClient, db) -> None:
        self.client, self.db = client, db


class MongoAdapter(DatabaseAdapter):
    engine: ClassVar[Engine] = Engine.MONGODB
    default_port: ClassVar[int | None] = 27017
    capabilities: ClassVar[frozenset[Capability]] = frozenset({
        Capability.STATUS_BASIC, Capability.SESSIONS, Capability.CACHE_HIT, Capability.REPLICATION,
        Capability.SCHEMA, Capability.TABLE_STATS, Capability.INDEX_USAGE, Capability.COLUMN_PROFILE,
        Capability.QUERY_CONSOLE, Capability.EXPLAIN, Capability.PRIVILEGE_CHECK,
    })  # fmt: skip

    @property
    def srv(self) -> bool:
        return str(self.cfg.options.get("srv", "")).lower() == "true"

    def _client(self) -> pymongo.MongoClient:
        opts = self.cfg.options
        kwargs: dict[str, Any] = {
            "username": self.cfg.username or None, "password": self.secret.password or None,
            "authSource": opts.get("authsource") or ("admin" if self.cfg.username else None),
            "tls": self.cfg.tls_mode != "disable",
            "serverSelectionTimeoutMS": self.cfg.timeout_s * 1000, "connectTimeoutMS": self.cfg.timeout_s * 1000,
            "socketTimeoutMS": MAX_TIME_MS + 5000, "appname": "rideops-lens", "readPreference": "secondaryPreferred",
            "retryWrites": False,
        }  # fmt: skip
        if self.cfg.tls_mode == "require":
            kwargs["tlsAllowInvalidCertificates"] = True
        elif self.cfg.tls_mode == "verify-ca":
            kwargs["tlsAllowInvalidHostnames"] = True
        if opts.get("replicaset"):
            kwargs["replicaSet"] = opts["replicaset"]
        kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if self.srv:
            return pymongo.MongoClient(f"mongodb+srv://{self.cfg.host}/", **kwargs)
        host = (
            self.cfg.pinned_ip
            if self.cfg.pinned_ip and self.cfg.tls_mode != "verify-full"
            else self.cfg.host
        )
        return pymongo.MongoClient(host=host, port=self.cfg.port or 27017, directConnection=not opts.get("replicaset"),
                                   **kwargs)  # fmt: skip

    @contextmanager
    def connect(self) -> Iterator[MongoSession]:
        client = self._client()
        try:
            client.admin.command("ping")
            yield MongoSession(client, client[self.cfg.database or "test"])
        except (merr.PyMongoError, merr.ConfigurationError) as exc:
            raise classify(exc) from exc
        finally:
            client.close()

    # ----------------------------------------------------------------------- test
    def test(self) -> TestResult:
        stages = [] if self.srv else [tcp_stage(self.cfg, 27017)]
        if stages and not stages[0].ok:
            return TestResult(False, stages)
        sw = Stopwatch()
        try:
            with self.connect() as s:
                ms = sw.ms
                tls_on = self.cfg.tls_mode != "disable"
                stages += [
                    StageResult(
                        "TLS",
                        True,
                        0,
                        f"TLS on ({self.cfg.tls_mode})" if tls_on else "TLS disabled (insecure)",
                        warning=not tls_on,
                    ),
                    StageResult(
                        "AUTH", True, ms, f"Signed in as {self.cfg.username or '(no auth)'}"
                    ),
                ]
                sw = Stopwatch()
                names = s.db.list_collection_names()
                version = s.client.server_info()["version"]
                stages.append(
                    StageResult(
                        "DB_SELECT", True, sw.ms, f"Database {s.db.name}: {len(names)} collections"
                    )
                )
                findings = self._privilege_findings(s)
                write = sorted({f["privilege"] for f in findings if f["severity"] == "warning"})
                stages.append(StageResult("PRIVILEGES", not write, 0,
                                          f"User can write ({', '.join(write[:5])}). Use a read-only user." if write
                                          else "Read-only user", "PRIV_WRITE_GRANTED" if write else None,
                                          warning=bool(write)))  # fmt: skip
                stages.append(
                    StageResult("CAPABILITIES", True, 0, f"{len(self.capabilities)} capabilities")
                )
                return TestResult(
                    True, stages, f"MongoDB {version}", set(self.capabilities), findings
                )
        except AdapterError as err:
            stages.append(StageResult(err.stage if err.stage in ("TLS", "AUTH", "TCP") else "AUTH", False, sw.ms,
                                      err.message, err.code))  # fmt: skip
            return TestResult(False, stages)

    def _privilege_findings(self, s: MongoSession) -> list[dict[str, Any]]:
        try:
            info = s.db.command("connectionStatus", showPrivileges=True)["authInfo"]
        except merr.PyMongoError:
            return [{"privilege": "UNKNOWN", "object": "*", "severity": "info"}]
        if not info.get("authenticatedUsers"):
            return [{"privilege": "NO_AUTH", "object": "*", "severity": "warning"}]
        found = set()
        for p in info.get("authenticatedUserPrivileges", []):
            for action in set(p.get("actions", [])) & WRITE_ACTIONS:
                res = p.get("resource", {})
                found.add(
                    (action, f"{res.get('db', '*') or '*'}.{res.get('collection', '*') or '*'}")
                )
        return [{"privilege": a, "object": o, "severity": "warning"} for a, o in sorted(found)][:50]

    def privilege_findings(self) -> list[dict[str, Any]]:
        with self.connect() as s:
            return self._privilege_findings(s)

    # --------------------------------------------------------------------- status
    def status(self) -> StatusSnapshot:
        sw = Stopwatch()
        try:
            with self.connect() as s:
                latency = sw.ms
                unavailable: dict[str, str] = {}
                try:
                    st = s.client.admin.command("serverStatus")
                except merr.OperationFailure:
                    st = {}
                    unavailable["connections"] = "needs the clusterMonitor role"
                stats = s.db.command("dbStats")
                conns = st.get("connections", {})
                cache = st.get("wiredTiger", {}).get("cache", {})
                used, maxb = (
                    cache.get("bytes currently in the cache"),
                    cache.get("maximum bytes configured"),
                )
                total = conns.get("current")
                cap = (conns.get("current", 0) + conns.get("available", 0)) or None
                metrics = {
                    "latency_ms": latency, "connections.total": total, "connections.active": conns.get("active"),
                    "connections.max": cap, "connections.used_pct": round(100.0 * total / cap, 2) if total and cap else None,
                    "db.size_bytes": int(stats.get("dataSize", 0) + stats.get("indexSize", 0)),
                    "uptime_s": int(st["uptime"]) if "uptime" in st else None,
                    "mongo.wt_cache_used_pct": round(100.0 * used / maxb, 2) if used and maxb else None,
                }  # fmt: skip
                for op, n in (st.get("opcounters") or {}).items():
                    metrics[f"mongo.opcounters.{op}"] = n
                return StatusSnapshot(taken_at=now(), reachable=True, latency_ms=latency,
                                      server_version=f"MongoDB {st.get('version') or s.client.server_info()['version']}",
                                      uptime_s=metrics["uptime_s"], db_size_bytes=metrics["db.size_bytes"],
                                      metrics=metrics, unavailable=unavailable)  # fmt: skip
        except AdapterError as err:
            return StatusSnapshot(taken_at=now(), reachable=False, latency_ms=None, error_code=err.code,
                                  error_message=err.message, error_stage=err.stage)  # fmt: skip

    # ------------------------------------------------------------------ analytics
    def list_tables(self, s: MongoSession) -> list[dict[str, Any]]:
        out = []
        for info in sorted(s.db.list_collections(), key=lambda c: c["name"]):
            name = info["name"]
            if name.startswith("system."):
                continue
            kind = "view" if info.get("type") == "view" else "collection"
            types: dict[str, Counter] = {}
            order: list[str] = []
            for doc in s.db[name].find({}, max_time_ms=MAX_TIME_MS).limit(INFER_SAMPLE):
                for k, v in doc.items():
                    if k not in types:
                        types[k] = Counter()
                        order.append(k)
                    types[k][bson_type(v)] += 1
            cols = [{"name": k, "data_type": types[k].most_common(1)[0][0], "nullable": True, "ordinal": i + 1,
                     "default": None, "comment": None, "is_pk": k == "_id"} for i, k in enumerate(order)]  # fmt: skip
            indexes = []
            if kind == "collection":
                for idx in s.db[name].list_indexes():
                    keys = list(idx["key"].keys())
                    indexes.append({"name": idx["name"], "columns": keys, "unique": bool(idx.get("unique")) or
                                    idx["name"] == "_id_", "primary": idx["name"] == "_id_", "bytes": None,
                                    "scans": None, "definition": json.dumps(idx["key"])})  # fmt: skip
            out.append(
                {
                    "schema": s.db.name,
                    "name": name,
                    "kind": kind,
                    "comment": None,
                    "columns": cols,
                    "indexes": indexes,
                    "foreign_keys": [],
                    "primary_key": ["_id"] if kind == "collection" else None,
                }
            )
        return out

    def table_stats(self, s: MongoSession) -> list[dict[str, Any]]:
        out = []
        for name in s.db.list_collection_names(filter={"type": "collection"}):
            if name.startswith("system."):
                continue
            try:
                st = s.db.command("collStats", name)
            except merr.OperationFailure:
                st = {"count": s.db[name].estimated_document_count()}
            out.append(
                {
                    "schema": s.db.name,
                    "name": name,
                    "row_estimate": st.get("count"),
                    "total_bytes": (st.get("storageSize") or 0) + (st.get("totalIndexSize") or 0)
                    or None,
                    "table_bytes": st.get("storageSize"),
                    "index_bytes": st.get("totalIndexSize"),
                    "dead_rows": None,
                    "live_rows": st.get("count"),
                    "seq_scans": None,
                    "idx_scans": None,
                    "inserts": None,
                    "updates": None,
                    "deletes": None,
                    "last_vacuum": None,
                    "last_analyze": None,
                }
            )
        return out

    def freshness_max(self, s: MongoSession, schema, table, column):
        doc = s.db[table].find_one(
            {column: {"$type": "date"}}, {column: 1}, sort=[(column, -1)], max_time_ms=MAX_TIME_MS
        )
        return doc.get(column) if doc else None

    def exact_count(self, s: MongoSession, schema, table) -> int:
        return int(s.db[table].count_documents({}, maxTimeMS=MAX_TIME_MS))

    def sample(self, s: MongoSession, schema, table, columns, n, row_estimate):
        projection = {c: 1 for c in columns}
        if "_id" not in columns:
            projection["_id"] = 0
        docs = list(
            s.db[table].aggregate(
                [{"$sample": {"size": int(n)}}, {"$project": projection}], maxTimeMS=MAX_TIME_MS
            )
        )
        return [{c: d.get(c) for c in columns} for d in docs], f"$sample {n}"

    def preview(self, s: MongoSession, schema, table, limit=50):
        docs = list(s.db[table].find({}, max_time_ms=MAX_TIME_MS).limit(limit))
        names: list[str] = []
        for d in docs:
            names += [k for k in d if k not in names]
        return names, [[jsonable_doc(d.get(k)) for k in names] for d in docs]

    def run_readonly(self, s: MongoSession, statement, *, wrap, max_rows, max_bytes, timeout_s):
        """`statement` is JSON already checked by mongo_guard.validate()."""
        spec = json.loads(statement)
        coll = s.db[spec["collection"]]
        started = time.perf_counter()
        ms = int(timeout_s) * 1000
        cap = min(
            int(spec.get("limit") or max_rows), max_rows
        )  # the user's limit, never above the row cap
        if "aggregate" in spec:
            cursor = coll.aggregate(list(spec["aggregate"]) + [{"$limit": cap + 1}], maxTimeMS=ms)
        else:
            cursor = coll.find(spec.get("find") or {}, spec.get("projection"), max_time_ms=ms)
            if spec.get("sort"):
                cursor = cursor.sort(list(spec["sort"].items()))
            cursor = cursor.limit(cap + 1)
        docs, size, truncated = [], 0, False
        for d in cursor:
            if len(docs) >= cap:
                truncated = cap == max_rows  # more rows existed than the console may return
                break
            d = jsonable_doc(d)
            size += len(json.dumps(d, default=str))
            if size > max_bytes:
                truncated = True
                break
            docs.append(d)
        names: list[str] = []
        for d in docs:
            names += [k for k in d if k not in names]
        return {"columns": [{"name": n, "type": ""} for n in names], "rows": [[d.get(n) for n in names] for d in docs],
                "row_count": len(docs), "truncated": truncated,
                "elapsed_ms": int((time.perf_counter() - started) * 1000)}  # fmt: skip

    def explain(self, s: MongoSession, statement):
        spec = json.loads(statement)
        if "aggregate" in spec:
            return jsonable_doc(
                s.db.command(
                    "aggregate", spec["collection"], pipeline=spec["aggregate"], explain=True
                )
            )
        return jsonable_doc(s.db[spec["collection"]].find(spec.get("find") or {}).explain())

    @staticmethod
    def readonly_grant_script(database: str, username: str) -> str:
        db = json.dumps(database or "your_database")
        user = json.dumps(username or "lens_ro")
        return f"""// Read-only user for RideOps Lens (run in mongosh against the admin database)
db.getSiblingDB("admin").createUser({{
  user: {user}, pwd: passwordPrompt(),
  roles: [{{ role: "read", db: {db} }}, {{ role: "clusterMonitor", db: "admin" }}]
}});
"""
