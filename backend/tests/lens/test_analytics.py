"""Phase 13 end-to-end: catalog sync, profiling, preview masking, query console, dashboards, insights."""

import psycopg
import pytest

from rideops.config import PostgresSettings
from rideops_api.jobs.worker import Worker
from rideops_api.lens.adapters import pg_catalog
from rideops_api.lens.adapters.base import ConnectionConfig, Engine, Secret
from rideops_api.lens.adapters.postgres import PostgresAdapter
from rideops_api.lens.catalog import detect
from rideops_api.lens.catalog.service import diff
from rideops_api.lens.queries import sqlguard
from rideops_api.settings import LensSettings
from tests.lens.conftest import TEST_DB, TEST_KEYS, sql
from tests.lens.test_connections import create, owner_ws

PG = PostgresSettings()
SETTINGS = LensSettings(lens_master_keys=TEST_KEYS)

SHOP_DDL = """
DROP SCHEMA IF EXISTS shop CASCADE;
CREATE SCHEMA shop;
CREATE TABLE shop.customers (
  id bigint PRIMARY KEY, full_name text NOT NULL, email text, phone text, city text,
  created_at timestamptz NOT NULL, updated_at timestamptz
);
CREATE TABLE shop.orders (
  id bigint PRIMARY KEY, customer_id bigint REFERENCES shop.customers(id), amount numeric(10,2),
  status text, created_at timestamptz NOT NULL
);
CREATE INDEX orders_status ON shop.orders (status);
CREATE INDEX orders_status_dup ON shop.orders (status);
CREATE TABLE shop.events_log (payload jsonb, logged_at timestamptz);
INSERT INTO shop.customers
  SELECT g, 'Customer ' || g, 'user' || g || '@example.com', '+91 98765 ' || lpad(g::text, 5, '0'),
         (ARRAY['BLR','MUM','DEL','PUN'])[1 + g %% 4], now() - (g || ' hours')::interval,
         now() - (g || ' minutes')::interval
    FROM generate_series(1, 2000) g;
INSERT INTO shop.orders
  SELECT g, 1 + g %% 2000, (g %% 500) + 0.99, (ARRAY['completed','cancelled','refunded'])[1 + g %% 3],
         now() - (g || ' minutes')::interval
    FROM generate_series(1, 20000) g;
INSERT INTO shop.events_log SELECT jsonb_build_object('n', g), now() - interval '30 days' FROM generate_series(1, 50) g;
ANALYZE shop.customers; ANALYZE shop.orders; ANALYZE shop.events_log;
"""


@pytest.fixture(scope="module")
def shop(lens_conninfo):
    with psycopg.connect(lens_conninfo, autocommit=True) as conn:
        conn.execute(SHOP_DDL.replace("%%", "%"))
    yield
    with psycopg.connect(lens_conninfo, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS shop CASCADE")


def drain(meta_db) -> int:
    return Worker(meta_db, settings=SETTINGS).drain(100)


@pytest.fixture
def ready(client, outbox, meta_db, shop):
    ws = owner_ws(client, outbox)
    cid = create(client, ws, name="Shop", environment="prod")["id"]
    drain(meta_db)  # initial health check + schema sync
    return ws, cid


def base(ws, cid):
    return f"/api/v1/workspaces/{ws}/connections/{cid}"


# ------------------------------------------------------------------ unit level


def test_pii_and_freshness_heuristics():
    assert (
        detect.is_pii_name("email")
        and detect.is_pii_name("customerEmail")
        and detect.is_pii_name("phone_number")
    )
    assert detect.is_pii_name("full_name") and detect.is_pii_name("aadhaar_no")
    assert (
        not detect.is_pii_name("campaign")
        and not detect.is_pii_name("table_name")
        and not detect.is_pii_name("amount")
    )
    table = {"columns": [{"name": "created_at", "data_type": "timestamp with time zone"},
                         {"name": "updated_at", "data_type": "timestamp with time zone"}], "indexes": []}  # fmt: skip
    assert detect.freshness_column(table) == "updated_at"
    assert (
        detect.freshness_column({"columns": [{"name": "x", "data_type": "text"}], "indexes": []})
        is None
    )


def test_schema_diff():
    old = {"s.t": {"kind": "table", "columns": [{"name": "a", "data_type": "int", "nullable": True},
                                                {"name": "b", "data_type": "text", "nullable": True}],
                   "indexes": [{"name": "i1", "columns": ["a"]}]},
           "s.gone": {"kind": "table", "columns": [], "indexes": []}}  # fmt: skip
    new = {
        "s.t": {
            "kind": "table",
            "columns": [
                {"name": "a", "data_type": "bigint", "nullable": False},
                {"name": "c", "data_type": "text", "nullable": True},
            ],
            "indexes": [{"name": "i2", "columns": ["c"]}],
        },
        "s.new": {
            "kind": "view",
            "columns": [{"name": "x", "data_type": "int", "nullable": True}],
            "indexes": [],
        },
    }
    kinds = sorted((c["change_type"], c["object_ref"]) for c in diff(old, new))
    assert kinds == sorted([("table_added", "s.new"), ("table_dropped", "s.gone"), ("column_added", "s.t.c"),
                            ("column_dropped", "s.t.b"), ("type_changed", "s.t.a"), ("nullability_changed", "s.t.a"),
                            ("index_added", "s.t#i2"), ("index_dropped", "s.t#i1")])  # fmt: skip


GUARD_ACCEPT = ["select 1", "with a as (select 1) select * from a", "select 1 union select 2",
                "explain select * from shop.orders", "show server_version",
                "select status, count(*) from shop.orders group by 1 order by 2 desc"]  # fmt: skip
GUARD_REJECT = [
    "delete from t", "insert into t values (1)", "update t set a = 1", "drop table t", "truncate t",
    "create table x (a int)", "alter table t add b int", "grant select on t to x", "copy t to stdout",
    "select 1; select 2", "select * from t; drop table t", "with x as (delete from t returning *) select * from x",
    "with x as (update t set a=1 returning *) select 1", "select * into t2 from t", "select * from t for update",
    "select * from t for share", "select pg_sleep(5)", "select pg_sleep_for('5 s')", "select dblink('h','q')",
    "select pg_read_file('/etc/passwd')", "select lo_import('/etc/passwd')", "select set_config('a','b',false)",
    "select nextval('s')", "select setval('s', 1)", "select pg_terminate_backend(1)", "select pg_advisory_lock(1)",
    "select query_to_xml('delete from t', true, true, '')", "explain analyze select 1",
    "explain analyze delete from t", "explain (analyze true) select 1", "set statement_timeout = 0", "begin", "commit",
    "call p()", "do $$ begin delete from t; end $$", "prepare p as delete from t", "execute p", "vacuum t",
    "lock table t", "refresh materialized view m", "listen c", "notify c", "show all", "",
    "merge into t using s on t.id = s.id when matched then delete", "select * from t where id = 1 for no key update",
]  # fmt: skip


def test_sql_guard_corpus_is_fully_enforced():
    """Phase 13 acceptance: SQL guard corpus 100 %."""
    assert all(sqlguard.validate(q).ok for q in GUARD_ACCEPT)
    leaked = [q for q in GUARD_REJECT if sqlguard.validate(q).ok]
    assert leaked == []


def test_database_layer_rejects_writes_even_without_the_guard(lens_conninfo, shop):
    """Layer 2 (§18.5): the adapter's session is read-only, so a write that slipped past the guard fails."""
    cfg = ConnectionConfig(
        None, Engine.POSTGRES, "localhost", PG.port, TEST_DB, PG.user, {}, "require", "127.0.0.1"
    )
    adapter = PostgresAdapter(cfg, Secret(password=PG.password.get_secret_value()))
    with adapter.connect() as t:
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            pg_catalog.run_readonly(t, "INSERT INTO shop.events_log VALUES ('{}', now())", wrap=False, max_rows=1,
                                    max_bytes=1000, timeout_s=5)  # fmt: skip
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            t.execute("DELETE FROM shop.events_log")
    assert sql(lens_conninfo, "SELECT count(*) AS n FROM shop.events_log")[0]["n"] == 50


# -------------------------------------------------------------------- end to end


def test_catalog_sync_detects_tables_pii_and_freshness(client, ready):
    ws, cid = ready
    cat = client.get(f"{base(ws, cid)}/catalog", params={"schema": "shop"}).json()
    names = {t["table_name"]: t for t in cat["tables"]}
    assert {"customers", "orders", "events_log"} <= set(names)
    assert set(names["customers"]["pii_columns"]) >= {"full_name", "email", "phone"}
    assert names["customers"]["freshness_column"] == "updated_at"
    assert (
        names["orders"]["row_estimate"] == 20000 and names["customers"]["freshness_max"] is not None
    )
    assert cat["last_sync"]["ok"] is True
    found = client.get(f"{base(ws, cid)}/catalog", params={"search": "email"}).json()["tables"]
    assert any(t["table_name"] == "customers" and "email" in t["matching_columns"] for t in found)
    detail = client.get(f"{base(ws, cid)}/tables/shop/orders").json()
    assert detail["table"]["primary_key"] == ["id"]
    assert detail["table"]["foreign_keys"][0]["ref_table"] == "shop.customers"
    assert detail["stats"]["row_estimate"] == 20000
    refs = client.get(f"{base(ws, cid)}/tables/shop/customers").json()["referenced_by"]
    assert refs and refs[0]["table_name"] == "orders"


def test_insights(client, ready):
    ws, cid = ready
    codes = {(i["code"], i["object_ref"]) for i in client.get(f"{base(ws, cid)}/insights").json()}
    assert ("NO_PRIMARY_KEY", "shop.events_log") in codes
    assert ("DUPLICATE_INDEX", "shop.orders#orders_status_dup") in codes
    assert ("FK_WITHOUT_INDEX", "shop.orders(customer_id)") in codes
    assert ("TABLE_STALE", "shop.events_log") in codes  # newest row 30 days old
    assert ("OVER_PRIVILEGED_ROLE", "") in codes  # the test role is a superuser
    assert ("TLS_WEAK", "") in codes  # prod + tls require
    first = client.get(f"{base(ws, cid)}/insights").json()[0]
    assert client.post(f"/api/v1/workspaces/{ws}/insights/{first['id']}/dismiss").status_code == 200
    assert first["id"] not in {i["id"] for i in client.get(f"{base(ws, cid)}/insights").json()}


def test_schema_change_detection(client, ready, meta_db, lens_conninfo):
    ws, cid = ready
    sql(lens_conninfo, "ALTER TABLE shop.customers ADD COLUMN loyalty_tier text")
    try:
        assert client.post(f"{base(ws, cid)}/catalog/sync").status_code == 202
        drain(meta_db)
        changes = client.get(f"{base(ws, cid)}/schema-changes").json()
        assert any(
            c["change_type"] == "column_added" and c["object_ref"] == "shop.customers.loyalty_tier"
            for c in changes
        )
        assert (
            client.post(f"{base(ws, cid)}/catalog/sync").status_code == 429
        )  # one manual sync per minute
    finally:
        sql(lens_conninfo, "ALTER TABLE shop.customers DROP COLUMN loyalty_tier")


def test_profile_and_auto_charts(client, ready, meta_db):
    ws, cid = ready
    assert client.post(f"{base(ws, cid)}/tables/shop/orders/profile").status_code == 202
    assert client.get(f"{base(ws, cid)}/tables/shop/orders/profile").json()["pending"] is True
    drain(meta_db)
    body = client.get(f"{base(ws, cid)}/tables/shop/orders/profile").json()
    prof = {p["column"]: p for p in body["profile"]["profiles"]}
    assert body["profile"]["sample_rows"] > 0 and not body["pending"]
    assert prof["status"]["distinct_est"] == 3 and prof["status"]["null_pct"] == 0
    assert {v for v, _ in prof["status"]["top_k"]} == {"completed", "cancelled", "refunded"}
    assert (
        prof["amount"]["kind"] == "numeric"
        and prof["amount"]["min"] >= 0.99
        and "quantiles" in prof["amount"]
    )
    charts = {c["column"]: c["type"] for c in body["profile"]["charts"]}
    assert (
        charts["status"] == "bar"
        and charts["amount"] == "histogram"
        and charts["created_at"] == "timeseries"
    )

    client.post(f"{base(ws, cid)}/tables/shop/customers/profile")
    drain(meta_db)
    cust = {
        p["column"]: p
        for p in client.get(f"{base(ws, cid)}/tables/shop/customers/profile").json()["profile"][
            "profiles"
        ]
    }
    # PII columns keep value-free stats only: no top values, no min/max.
    assert cust["email"]["pii"] and "top_k" not in cust["email"] and "min" not in cust["email"]
    assert cust["email"]["patterns"]["email"] == 1.0
    assert "example.com" not in str(cust["email"]) and "Customer 1" not in str(cust["full_name"])
    exported = client.get(f"{base(ws, cid)}/tables/shop/orders/profile/export.json")
    assert exported.status_code == 200 and "attachment" in exported.headers["content-disposition"]


def test_preview_masks_pii_and_reveal_is_audited(client, ready):
    ws, cid = ready
    p = client.get(f"{base(ws, cid)}/tables/shop/customers/preview").json()
    email_idx = p["columns"].index("email")
    assert len(p["rows"]) == 50 and all(r[email_idx] == "•••" for r in p["rows"])
    assert "email" in p["masked_columns"]
    city_idx = p["columns"].index("city")
    assert p["rows"][0][city_idx] in ("BLR", "MUM", "DEL", "PUN")
    revealed = client.get(
        f"{base(ws, cid)}/tables/shop/customers/preview", params={"reveal": "true"}
    ).json()
    assert revealed["rows"][0][email_idx].endswith("@example.com")
    audit = client.get(f"/api/v1/workspaces/{ws}/audit", params={"action": "pii"}).json()["items"]
    assert audit and audit[0]["action"] == "pii.reveal"


def test_exact_count_and_stats_history(client, ready):
    ws, cid = ready
    assert client.post(f"{base(ws, cid)}/tables/shop/orders/count").json() == {"row_exact": 20000}
    hist = client.get(f"{base(ws, cid)}/tables/shop/orders/stats/history").json()
    assert hist and hist[-1]["row_exact"] == 20000


def test_query_console(client, ready):
    ws, cid = ready
    q = f"{base(ws, cid)}/queries"
    v = client.post(f"{q}/validate", json={"statement": "select * from shop.orders"}).json()
    assert v["ok"] and v["tables"] == ["shop.orders"]
    bad = client.post(f"{q}/validate", json={"statement": "delete from shop.orders"}).json()
    assert not bad["ok"] and bad["code"] == "QUERY_NOT_READONLY"

    r = client.post(
        f"{q}/run",
        json={"statement": "select status, count(*) as n from shop.orders group by 1 order by 1"},
    )
    body = r.json()
    assert (
        r.status_code == 200
        and body["row_count"] == 3
        and [c["name"] for c in body["columns"]] == ["status", "n"]
    )
    capped = client.post(
        f"{q}/run", json={"statement": "select * from shop.orders", "max_rows": 100}
    ).json()
    assert capped["row_count"] == 100 and capped["truncated"] is True

    masked = client.post(
        f"{q}/run", json={"statement": "select id, email from shop.customers order by id limit 3"}
    ).json()
    assert masked["masked_columns"] == ["email"] and all(row[1] == "•••" for row in masked["rows"])
    shown = client.post(f"{q}/run", json={"statement": "select id, email from shop.customers order by id limit 1",
                                          "reveal": True}).json()  # fmt: skip
    assert shown["rows"][0][1] == "user1@example.com"

    rejected = client.post(f"{q}/run", json={"statement": "update shop.orders set status = 'x'"})
    assert rejected.status_code == 422 and rejected.json()["error"]["code"] == "QUERY_NOT_READONLY"
    broken = client.post(f"{q}/run", json={"statement": "select nope from shop.orders"})
    assert broken.status_code == 400 and broken.json()["error"]["code"] == "QUERY_ERROR"
    plan = client.post(
        f"{q}/explain", json={"statement": "select * from shop.orders where status = 'completed'"}
    ).json()
    assert plan["plan"][0]["Plan"]["Node Type"]

    csv_resp = client.get(f"/api/v1/workspaces/{ws}/query-runs/{masked['run_id']}/export.csv")
    lines = csv_resp.text.strip().splitlines()
    assert csv_resp.headers["content-type"].startswith("text/csv") and lines[0] == "id,email"
    assert lines[1] == "1,•••"  # exports respect masking (FR-36)
    runs = client.get(f"/api/v1/workspaces/{ws}/query-runs", params={"connection_id": cid}).json()
    statuses = {r["status"] for r in runs}
    assert {"ok", "rejected", "error"} <= statuses


def test_saved_queries_and_dashboards(client, ready, new_client, meta_db):
    ws, cid = ready
    r = client.post(f"/api/v1/workspaces/{ws}/saved-queries", json={"connection_id": cid, "name": "Orders by status",
                    "statement": "select status, count(*) as n from shop.orders group by 1 order by 1"})  # fmt: skip
    assert r.status_code == 201
    qid = r.json()["id"]
    assert client.post(f"/api/v1/workspaces/{ws}/saved-queries", json={"connection_id": cid, "name": "x",
                       "statement": "drop table shop.orders"}).status_code == 422  # fmt: skip
    d = client.post(
        f"/api/v1/workspaces/{ws}/dashboards", json={"name": "Ops", "refresh_interval_s": 900}
    ).json()
    w = client.post(
        f"/api/v1/workspaces/{ws}/dashboards/{d['id']}/widgets",
        json={
            "kind": "bar",
            "title": "Orders",
            "saved_query_id": qid,
            "options": {"x": "status", "y": "n"},
        },
    )
    assert w.status_code == 201
    refreshed = client.post(f"/api/v1/workspaces/{ws}/dashboards/{d['id']}/refresh").json()
    assert refreshed["refresh"] == {"ok": 1, "failed": 0}
    widget = refreshed["widgets"][0]
    assert widget["result_cache"]["row_count"] == 3 and widget["stale"] is False
    got = client.get(f"/api/v1/workspaces/{ws}/dashboards/{d['id']}").json()
    assert got["widgets"][0]["result_cache"]["rows"]
    auto = client.get(f"{base(ws, cid)}/dashboard").json()
    assert auto["largest_tables"] and auto["connection"]["name"] == "Shop"
    assert any(f["table_name"] == "events_log" for f in auto["freshness"])
    # Scheduled refresh: the system job picks up dashboards whose interval elapsed.
    from rideops_api.jobs import queue

    with meta_db.pool.connection() as conn:
        conn.execute(
            "UPDATE app.dashboards SET last_refreshed_at = now() - interval '1 hour' WHERE id = %s",
            (d["id"],),
        )
        queue.enqueue(conn, "dashboards_due")
    drain(meta_db)
    assert (
        client.get(f"/api/v1/workspaces/{ws}/dashboards/{d['id']}").json()["last_refreshed_at"]
        > d["created_at"]
    )
    # Other workspaces can't see any of it.
    stranger = new_client()
    from tests.lens.conftest import signup

    signup(stranger)
    assert stranger.get(f"/api/v1/workspaces/{ws}/dashboards/{d['id']}").status_code == 404
    assert stranger.get(f"/api/v1/workspaces/{ws}/saved-queries/{qid}").status_code == 404
    assert client.delete(f"/api/v1/workspaces/{ws}/saved-queries/{qid}").status_code == 200
    assert client.get(f"/api/v1/workspaces/{ws}/dashboards/{d['id']}").json()["widgets"] == []
