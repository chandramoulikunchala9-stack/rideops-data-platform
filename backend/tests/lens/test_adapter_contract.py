# ruff: noqa: E501 - engine fixtures read best as one connection spec per line
"""Adapter contract suite (§11, §25.2): the same assertions against every engine.

Each engine is seeded with the same `orders` data (2 000 rows: status completed/cancelled 50/50,
amount = i % 50 + 0.5). Engines whose server isn't reachable are skipped, so the suite runs
anywhere; CI and this repo's local runs use Docker containers:

    docker run -d -p 3307:3306 -e MYSQL_ROOT_PASSWORD=RootPass-123 -e MYSQL_DATABASE=shop mysql:8.4
    docker run -d -p 1434:1433 -e ACCEPT_EULA=Y -e 'MSSQL_SA_PASSWORD=SaPass-12345!' \
        mcr.microsoft.com/mssql/server:2022-latest
    docker run -d -p 27018:27017 -e MONGO_INITDB_ROOT_USERNAME=root -e MONGO_INITDB_ROOT_PASSWORD=RootPass-123 mongo:8.0

Override with LENS_CONTRACT_MYSQL / _MSSQL / _MONGO = "host:port:user:password".
"""

import json
import os
import socket
import sqlite3
from datetime import datetime, timezone

import pytest

from rideops.config import PostgresSettings
from rideops_api.lens.adapters.base import ConnectionConfig, Engine, Secret
from rideops_api.lens.adapters.registry import ADAPTERS
from rideops_api.lens.profiling.service import profile_column
from rideops_api.lens.queries import service as queries
from tests.lens.conftest import TEST_DB

N = 2000


def _target(env: str, default: str) -> tuple[str, int, str, str]:
    host, port, user, password = os.environ.get(env, default).split(":", 3)
    return host, int(port), user, password


def _reachable(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def seed_postgres(conninfo: str) -> None:
    import psycopg

    with psycopg.connect(conninfo, autocommit=True) as c:
        c.execute("DROP SCHEMA IF EXISTS contract CASCADE; CREATE SCHEMA contract")
        c.execute(
            "CREATE TABLE contract.orders (id int PRIMARY KEY, status text, amount numeric(10,2), created_at timestamptz)"
        )
        c.execute(f"INSERT INTO contract.orders SELECT g, CASE g % 2 WHEN 0 THEN 'completed' ELSE 'cancelled' END, "
                  f"g % 50 + 0.5, now() FROM generate_series(1, {N}) g; ANALYZE contract.orders")  # fmt: skip


def seed_mysql(host, port, user, password) -> None:
    import pymysql

    c = pymysql.connect(host=host, port=port, user=user, password=password, autocommit=True)
    with c.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS contract")
        cur.execute("DROP TABLE IF EXISTS contract.orders")
        cur.execute(
            "CREATE TABLE contract.orders (id INT PRIMARY KEY, status VARCHAR(20), amount DECIMAL(10,2), created_at DATETIME)"
        )
        cur.executemany(
            "INSERT INTO contract.orders VALUES (%s, %s, %s, NOW())",
            [
                (i, "completed" if i % 2 == 0 else "cancelled", i % 50 + 0.5)
                for i in range(1, N + 1)
            ],
        )
        cur.execute("ANALYZE TABLE contract.orders")
    c.close()


def seed_mssql(host, port, user, password) -> None:
    import pymssql

    c = pymssql.connect(
        server=host, port=port, user=user, password=password, database="master", autocommit=True
    )
    c.cursor().execute("IF DB_ID('contract') IS NULL CREATE DATABASE contract")
    c.close()
    c = pymssql.connect(
        server=host, port=port, user=user, password=password, database="contract", autocommit=True
    )
    cur = c.cursor()
    cur.execute("IF OBJECT_ID('dbo.orders') IS NOT NULL DROP TABLE dbo.orders")
    cur.execute(
        "CREATE TABLE dbo.orders (id INT PRIMARY KEY, status NVARCHAR(20), amount DECIMAL(10,2), created_at DATETIME2)"
    )
    cur.execute(f"""INSERT INTO dbo.orders SELECT n, CASE n % 2 WHEN 0 THEN 'completed' ELSE 'cancelled' END,
                    n % 50 + 0.5, SYSDATETIME() FROM (SELECT TOP {N} ROW_NUMBER() OVER (ORDER BY (SELECT 1)) n
                    FROM sys.all_objects a CROSS JOIN sys.all_objects b) x""")  # fmt: skip
    c.close()


def seed_mongo(host, port, user, password) -> None:
    import pymongo

    m = pymongo.MongoClient(host=host, port=port, username=user, password=password, authSource="admin",
                            serverSelectionTimeoutMS=3000)  # fmt: skip
    db = m.contract
    db.orders.drop()
    now = datetime.now(timezone.utc)
    db.orders.insert_many([{"_id": i, "status": "completed" if i % 2 == 0 else "cancelled", "amount": i % 50 + 0.5,
                            "created_at": now} for i in range(1, N + 1)])  # fmt: skip
    m.close()


def seed_sqlite(path: str) -> None:
    c = sqlite3.connect(path)
    c.execute(
        "CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount REAL, created_at TEXT)"
    )
    c.executemany("INSERT INTO orders VALUES (?, ?, ?, datetime('now'))",
                  [(i, "completed" if i % 2 == 0 else "cancelled", i % 50 + 0.5) for i in range(1, N + 1)])  # fmt: skip
    c.commit()
    c.close()


def _engines(tmp_dir) -> dict:
    pg = PostgresSettings()
    out = {
        "postgres": dict(
            cfg=ConnectionConfig(
                None,
                Engine.POSTGRES,
                "localhost",
                pg.port,
                TEST_DB,
                pg.user,
                {},
                "require",
                "127.0.0.1",
            ),
            secret=Secret(password=pg.password.get_secret_value()),
            schema="contract",
            table="orders",
            seed=lambda: seed_postgres(pg.conninfo(TEST_DB)),
            write="DELETE FROM contract.orders",
            read="SELECT status, count(*) AS n FROM contract.orders GROUP BY status",
        )
    }
    h, p, u, pw = _target("LENS_CONTRACT_MYSQL", "127.0.0.1:3307:root:RootPass-123")
    out["mysql"] = dict(
        cfg=ConnectionConfig(None, Engine.MYSQL, "localhost", p, "contract", u, {}, "require", h),
        secret=Secret(password=pw),
        schema="contract",
        table="orders",
        seed=lambda: seed_mysql(h, p, u, pw),
        write="DELETE FROM orders",
        read="SELECT status, count(*) AS n FROM orders GROUP BY status",
        hp=(h, p),
    )
    h2, p2, u2, pw2 = _target("LENS_CONTRACT_MSSQL", "127.0.0.1:1434:sa:SaPass-12345!")
    out["mssql"] = dict(cfg=ConnectionConfig(None, Engine.MSSQL, "localhost", p2, "contract", u2, {}, "require", h2),
                        secret=Secret(password=pw2), schema="dbo", table="orders", seed=lambda: seed_mssql(h2, p2, u2, pw2),
                        write="DELETE FROM dbo.orders", read="SELECT status, count(*) AS n FROM dbo.orders GROUP BY status",
                        hp=(h2, p2))  # fmt: skip
    h3, p3, u3, pw3 = _target("LENS_CONTRACT_MONGO", "127.0.0.1:27018:root:RootPass-123")
    out["mongodb"] = dict(cfg=ConnectionConfig(None, Engine.MONGODB, "localhost", p3, "contract", u3, {"authsource": "admin"},
                                               "disable", h3), secret=Secret(password=pw3), schema="contract", table="orders",
                          seed=lambda: seed_mongo(h3, p3, u3, pw3),
                          write=json.dumps({"collection": "orders", "aggregate": [{"$out": "stolen"}]}),
                          read=json.dumps({"collection": "orders", "aggregate": [{"$group": {"_id": "$status", "n": {"$sum": 1}}}]}),
                          hp=(h3, p3))  # fmt: skip
    path = os.path.join(tmp_dir, "contract.sqlite")
    out["sqlite"] = dict(
        cfg=ConnectionConfig(
            None, Engine.SQLITE, None, None, "upload", None, {"path": path}, "disable", None
        ),
        secret=Secret(),
        schema="main",
        table="orders",
        seed=lambda: seed_sqlite(path),
        write="DELETE FROM orders",
        read="SELECT status, count(*) AS n FROM orders GROUP BY status",
    )
    return out


ENGINE_NAMES = ["postgres", "mysql", "mssql", "mongodb", "sqlite"]


@pytest.fixture(scope="module")
def engines(tmp_path_factory, lens_conninfo):
    return _engines(str(tmp_path_factory.mktemp("contract")))


@pytest.fixture(params=ENGINE_NAMES)
def eng(request, engines):
    name = request.param
    e = engines[name]
    if name not in ADAPTERS:
        pytest.skip(f"{name} driver not installed")
    if "hp" in e and not _reachable(*e["hp"]):
        pytest.skip(f"{name} server not reachable at {e['hp'][0]}:{e['hp'][1]}")
    if not e.get("seeded"):
        e["seed"]()
        e["seeded"] = True
    e["adapter"] = ADAPTERS[name](e["cfg"], e["secret"])
    e["name"] = name
    return e


def test_connection_test_passes(eng):
    result = eng["adapter"].test()
    assert result.ok, [s.as_dict() for s in result.stages]
    assert result.server_version and result.capabilities
    assert {s.stage for s in result.stages} >= {
        "TLS",
        "AUTH",
        "DB_SELECT",
        "PRIVILEGES",
        "CAPABILITIES",
    }


def test_status_has_basic_keys(eng):
    snap = eng["adapter"].status()
    assert snap.reachable and snap.error_code is None
    assert snap.latency_ms is not None and snap.server_version
    assert "latency_ms" in snap.metrics and "db.size_bytes" in snap.metrics


def test_seeded_table_in_catalog_and_stats(eng):
    a = eng["adapter"]
    with a.connect() as s:
        tables = {t["name"]: t for t in a.list_tables(s) if t["schema"] == eng["schema"]}
        assert eng["table"] in tables
        cols = {c["name"] for c in tables[eng["table"]]["columns"]}
        assert {"status", "amount", "created_at"} <= cols
        stats = {t["name"]: t for t in a.table_stats(s) if t["schema"] == eng["schema"]}
        assert stats[eng["table"]]["row_estimate"] is not None
        assert a.exact_count(s, eng["schema"], eng["table"]) == N
        names, rows = a.preview(s, eng["schema"], eng["table"], 5)
        assert len(rows) == 5 and "status" in names
        assert a.freshness_max(s, eng["schema"], eng["table"], "created_at") is not None


def test_profile_of_seeded_column_matches_expected(eng):
    """amount = i % 50 + 0.5 → mean ≈ 25.0 (±5 %); status → 2 values ~50 % each."""
    a = eng["adapter"]
    with a.connect() as s:
        rows, method = a.sample(s, eng["schema"], eng["table"], ["status", "amount"], 1000, N)
    assert len(rows) >= 500, method
    amount, _ = profile_column("amount", "numeric", [r["amount"] for r in rows], pii_by_name=False)
    assert amount["mean"] == pytest.approx(25.0, rel=0.05)
    status, _ = profile_column("status", "text", [r["status"] for r in rows], pii_by_name=False)
    assert status["distinct_est"] == 2 and status["null_pct"] == 0


def test_reads_work_and_writes_are_rejected_twice(eng):
    a = eng["adapter"]
    read = queries.validate_for(eng["name"], eng["read"])
    assert read.ok
    with a.connect() as s:
        result = a.run_readonly(s, read.statement if eng["name"] == "mongodb" else eng["read"], wrap=True, max_rows=10,
                                max_bytes=10**6, timeout_s=10)  # fmt: skip
        counts = {r[0]: r[1] for r in result["rows"]}
        assert counts == {"completed": N // 2, "cancelled": N // 2}
        assert not queries.validate_for(eng["name"], eng["write"]).ok  # layer 3: the guard
        if eng["name"] == "mongodb":
            return  # Mongo has no write statement to send: the guard is the only way in, and it's closed
        with pytest.raises(
            queries.DRIVER_ERRORS
        ):  # layer 2: the session itself refuses (or rolls back)
            a.run_readonly(s, eng["write"], wrap=False, max_rows=1, max_bytes=1000, timeout_s=10)
            if eng["name"] == "mssql":  # SQL Server runs it inside BEGIN TRAN … ROLLBACK instead
                raise queries.DRIVER_ERRORS[0]("rolled back")
        assert a.exact_count(s, eng["schema"], eng["table"]) == N  # nothing was deleted
