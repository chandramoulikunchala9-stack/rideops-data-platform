"""End-to-end: connections + status API against the local PostgreSQL as a target database."""

import re
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from rideops.config import PostgresSettings
from rideops_api.jobs.worker import Worker
from rideops_api.main import create_app
from tests.lens.conftest import PG_SSLMODE, TEST_DB, TEST_KEYS, signup, sql, unique_email

PG = PostgresSettings()
TARGET_URL = f"postgresql://{PG.user}@localhost:{PG.port}/{TEST_DB}?sslmode={PG_SSLMODE}"
SECRET = {"password": PG.password.get_secret_value()}


def _token(outbox, to: str) -> str:
    mail = next(m for m in reversed(outbox.outbox) if m.to == to)
    return re.search(r"token=([\w-]+)", mail.body).group(1)


def ws_settings(**settings) -> dict:
    """Workspace settings for tests; a test server without TLS (CI) needs the owner's opt-in."""
    if PG_SSLMODE == "disable":
        settings.setdefault("allow_insecure_connections", True)
    return settings


def owner_ws(client, outbox, *, allow_localhost: bool = True) -> str:
    me = signup(client)
    assert (
        client.post(
            "/api/v1/auth/verify-email", json={"token": _token(outbox, me["email"])}
        ).status_code
        == 200
    )
    ws = me["current_workspace_id"]
    # The workspace egress allowlist is how an owner permits a private host.
    settings = ws_settings(egress_allowlist=["localhost"] if allow_localhost else [])
    if settings != {"egress_allowlist": []}:
        r = client.patch(f"/api/v1/workspaces/{ws}", json={"settings": settings})
        assert r.status_code == 200, r.text
    return ws


def test_draft(client, ws, *, url=TARGET_URL, secret=SECRET, **extra) -> dict:
    r = client.post(
        f"/api/v1/workspaces/{ws}/connections/test", json={"url": url, "secret": secret, **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()


test_draft.__test__ = False  # helper, not a test


def create(client, ws, name="Shop DB", **extra) -> dict:
    t = test_draft(client, ws)
    assert t["ok"], t
    body = {
        "test_id": t["test_id"],
        "draft": {"url": TARGET_URL, "secret": SECRET},
        "name": name,
        **extra,
    }
    r = client.post(f"/api/v1/workspaces/{ws}/connections", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def run_jobs(meta_db) -> int:
    from rideops_api.settings import LensSettings

    return Worker(meta_db, settings=LensSettings(lens_master_keys=TEST_KEYS)).drain(50)


@pytest.fixture(autouse=True)
def clean_jobs(lens_conninfo):
    sql(lens_conninfo, "DELETE FROM app.jobs")


def test_parse_url_endpoint(client, outbox):
    ws = owner_ws(client, outbox)
    r = client.post(f"/api/v1/workspaces/{ws}/connections/parse-url",
                    json={"url": "postgresql://ro:hunter2-secret@db.example.com/shop?sslmode=verify-full"})  # fmt: skip
    body = r.json()
    assert r.status_code == 200 and body["has_password"] is True and "hunter2" not in r.text
    assert body["display_url"] == "postgresql://ro@db.example.com:5432/shop"
    r = client.post(
        f"/api/v1/workspaces/{ws}/connections/parse-url",
        json={"url": "postgresql://u@h/d?host=/tmp"},
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "OPTION_NOT_ALLOWED"


def test_staged_test_success_and_grant_script(client, outbox):
    ws = owner_ws(client, outbox)
    t = test_draft(client, ws)
    assert t["ok"] and t["server_version"].startswith("PostgreSQL")
    assert [s["stage"] for s in t["stages"]] == ["DNS", "NETWORK_POLICY", "TCP", "TLS", "AUTH", "DB_SELECT",
                                                 "PRIVILEGES", "CAPABILITIES"]  # fmt: skip
    priv = next(s for s in t["stages"] if s["stage"] == "PRIVILEGES")
    assert priv["code"] == "PRIV_WRITE_GRANTED" and priv["warning"]  # the test role is a superuser
    assert "default_transaction_read_only = on" in t["grant_script"]
    assert "status_basic" in t["capabilities"]
    assert SECRET["password"] not in str(t)


def test_staged_test_failures(client, outbox):
    ws = owner_ws(client, outbox, allow_localhost=False)
    blocked = test_draft(client, ws)
    assert not blocked["ok"] and blocked["stages"][-1]["code"] == "CONN_BLOCKED_NETWORK"
    audit = client.get(f"/api/v1/workspaces/{ws}/audit", params={"action": "network"}).json()[
        "items"
    ]
    assert audit and audit[0]["action"] == "network.blocked"
    ws = owner_ws(client, outbox)
    bad = test_draft(client, ws, secret={"password": "wrong"})
    assert not bad["ok"] and bad["stages"][-1] == {
        **bad["stages"][-1],
        "stage": "AUTH",
        "code": "CONN_AUTH_FAILED",
    }
    nodb = test_draft(client, ws, url=TARGET_URL.replace(TEST_DB, "no_such_db"))
    assert nodb["stages"][-1]["code"] == "CONN_DB_NOT_FOUND"
    port = test_draft(client, ws, url=TARGET_URL.replace(f":{PG.port}/", ":2222/"))
    assert port["stages"][-1]["code"] == "CONN_PORT_NOT_ALLOWED"
    strict = {"egress_allowlist": ["localhost"], "allow_insecure_connections": False}
    assert client.patch(f"/api/v1/workspaces/{ws}", json={"settings": strict}).status_code == 200
    insecure = test_draft(client, ws, tls_mode="disable")
    assert (
        insecure["stages"][-1]["code"] == "CONN_TLS_REQUIRED"
    )  # FR-14: insecure needs owner opt-in
    from rideops_api.lens.adapters import registry

    saved = registry.ADAPTERS.pop("mysql")  # simulate a server installed without the MySQL extra
    try:
        r = client.post(
            f"/api/v1/workspaces/{ws}/connections/test", json={"url": "mysql://u@db.example.com/x"}
        )
        assert r.status_code == 500 and r.json()["error"]["code"] == "DRIVER_MISSING"
    finally:
        registry.ADAPTERS["mysql"] = saved


def test_create_requires_matching_passing_test_and_verified_email(client, new_client, outbox):
    ws = owner_ws(client, outbox)
    t = test_draft(client, ws)
    other_secret = {"url": TARGET_URL, "secret": {"password": "a-different-one"}}
    r = client.post(f"/api/v1/workspaces/{ws}/connections", json={"test_id": t["test_id"], "draft": other_secret,
                                                                   "name": "X"})  # fmt: skip
    assert r.status_code == 409 and r.json()["error"]["code"] == "TEST_REQUIRED"
    r = client.post(f"/api/v1/workspaces/{ws}/connections", json={"test_id": str(uuid.uuid4()),
                    "draft": {"url": TARGET_URL, "secret": SECRET}, "name": "X"})  # fmt: skip
    assert r.status_code == 409
    failing = test_draft(client, ws, secret={"password": "wrong"})
    body = {
        "test_id": failing["test_id"],
        "draft": {"url": TARGET_URL, "secret": {"password": "wrong"}},
        "name": "F",
    }
    assert (
        client.post(f"/api/v1/workspaces/{ws}/connections", json=body).json()["error"]["code"]
        == "TEST_FAILED"
    )
    forced = client.post(
        f"/api/v1/workspaces/{ws}/connections",
        json={**body, "force_reason": "DB is in maintenance"},
    )
    assert forced.status_code == 201  # admins/owners may force-save with a logged reason
    unverified = new_client()
    me = signup(unverified)
    r = unverified.post(
        f"/api/v1/workspaces/{me['current_workspace_id']}/connections",
        json={"test_id": t["test_id"], "draft": {"url": TARGET_URL, "secret": SECRET}, "name": "Y"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"


def test_full_lifecycle(client, outbox, meta_db, lens_conninfo):
    ws = owner_ws(client, outbox)
    c = create(client, ws, environment="prod", tags=["shop"], check_interval_s=60)
    cid = c["id"]
    assert c["display_url"].startswith("postgresql://") and SECRET["password"] not in str(c)
    assert c["current_state"] == "unknown" and c["environment"] == "prod"
    # The secret is encrypted at rest: neither the password nor the URL appears in the stored blob.
    blob = sql(lens_conninfo, "SELECT ciphertext, wrapped_dek, kek_version FROM app.connection_secrets "
                              "WHERE connection_id = %s", cid)[0]  # fmt: skip
    assert SECRET["password"].encode() not in bytes(blob["ciphertext"]) and blob["kek_version"] == 2
    assert client.post(f"/api/v1/workspaces/{ws}/connections", json={
        "test_id": test_draft(client, ws)["test_id"], "draft": {"url": TARGET_URL, "secret": SECRET},
        "name": "shop db"}).json()["error"]["code"] == "NAME_TAKEN"  # fmt: skip

    # The initial health check was queued on create.
    assert run_jobs(meta_db) >= 1
    st = client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/status").json()
    latest = st["latest"]
    assert (
        latest["reachable"]
        and latest["state"] in ("healthy", "degraded")
        and latest["health_score"] >= 50
    )
    assert latest["metrics"]["connections.max"] > 0 and st["connection"][
        "server_version"
    ].startswith("PostgreSQL")
    assert st["uptime_24h"] == 100.0

    # Check now: queued once, then rate-limited within the same 10 s bucket.
    assert client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/status/check").status_code == 202
    assert client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/status/check").status_code == 429
    run_jobs(meta_db)
    hist = client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/status/history").json()
    assert len(hist) == 2 and all(p["up_checks"] == 1 for p in hist)
    assert client.get(
        f"/api/v1/workspaces/{ws}/connections/{cid}/status/history", params={"grain": "hour"}
    ).json()
    up = client.get(
        f"/api/v1/workspaces/{ws}/connections/{cid}/uptime", params={"window": "7d"}
    ).json()
    assert up["uptime_pct"] == 100.0 and up["checks"] == 2
    listed = client.get(
        f"/api/v1/workspaces/{ws}/connections", params={"env": "prod", "tag": "shop"}
    ).json()
    assert [x["id"] for x in listed] == [cid] and len(listed[0]["sparkline"]) == 2
    overview = client.get(f"/api/v1/workspaces/{ws}/status/overview").json()
    assert overview["total"] == 1 and sum(overview["by_state"].values()) == 1

    # Patch metadata and interval.
    r = client.patch(
        f"/api/v1/workspaces/{ws}/connections/{cid}",
        json={"name": "Shop (primary)", "check_interval_s": 300},
    )
    assert r.status_code == 200 and r.json()["check_interval_s"] == 300
    assert (
        sql(lens_conninfo, "SELECT interval_s FROM app.schedules WHERE connection_id = %s", cid)[0][
            "interval_s"
        ]
        == 300
    )

    # Pause: no checks are queued or run (FR-19); resume brings it back.
    assert (
        client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/pause").json()["current_state"]
        == "paused"
    )
    assert client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/status/check").status_code == 409
    sql(
        lens_conninfo,
        "INSERT INTO app.jobs (job_type, connection_id) VALUES ('health_check', %s)",
        cid,
    )
    before = sql(
        lens_conninfo,
        "SELECT count(*) AS n FROM app.status_snapshots WHERE connection_id = %s",
        cid,
    )[0]["n"]
    run_jobs(meta_db)
    assert (
        sql(
            lens_conninfo,
            "SELECT count(*) AS n FROM app.status_snapshots WHERE connection_id = %s",
            cid,
        )[0]["n"]
        == before
    )
    assert (
        client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/resume").json()["current_state"]
        == "unknown"
    )

    # Re-test the saved connection (secret decrypted server-side only).
    assert client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/test").json()["ok"]

    # Rotate: a failing new secret is refused; a tested one replaces it.
    bad = test_draft(client, ws, secret={"password": "wrong"})
    r = client.post(
        f"/api/v1/workspaces/{ws}/connections/{cid}/rotate-secret",
        json={
            "test_id": bad["test_id"],
            "draft": {"url": TARGET_URL, "secret": {"password": "wrong"}},
        },
    )
    assert r.status_code == 409
    good = test_draft(client, ws)
    r = client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/rotate-secret",
                    json={"test_id": good["test_id"], "draft": {"url": TARGET_URL, "secret": SECRET}})  # fmt: skip
    assert r.status_code == 200
    assert bytes(sql(lens_conninfo, "SELECT ciphertext FROM app.connection_secrets WHERE connection_id = %s",
                     cid)[0]["ciphertext"]) != bytes(blob["ciphertext"])  # fmt: skip

    # Delete crypto-shreds the secret immediately; restore needs a newly tested secret.
    assert client.delete(f"/api/v1/workspaces/{ws}/connections/{cid}").status_code == 200
    assert not sql(
        lens_conninfo, "SELECT 1 FROM app.connection_secrets WHERE connection_id = %s", cid
    )
    assert client.get(f"/api/v1/workspaces/{ws}/connections/{cid}").status_code == 404
    fresh = test_draft(client, ws)
    r = client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/restore",
                    json={"test_id": fresh["test_id"], "draft": {"url": TARGET_URL, "secret": SECRET}})  # fmt: skip
    assert r.status_code == 200 and r.json()["deleted_at"] is None
    assert (
        client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/grant-script").json()["engine"]
        == "postgres"
    )
    actions = {
        e["action"]
        for e in client.get(f"/api/v1/workspaces/{ws}/audit", params={"limit": 200}).json()["items"]
    }
    assert {"connection.create", "connection.update", "connection.pause", "connection.resume", "secret.rotate",
            "connection.delete", "connection.restore", "status.check"} <= actions  # fmt: skip
    # Secrets never leave through the audit log either.
    assert SECRET["password"] not in str(
        client.get(f"/api/v1/workspaces/{ws}/audit", params={"limit": 200}).json()
    )


def test_unhealthy_target_states(client, outbox, meta_db, lens_conninfo):
    ws = owner_ws(client, outbox)
    cid = create(client, ws, name="Will break")["id"]
    run_jobs(meta_db)
    # Simulate a credential change on the server side: store a wrong password for this connection.
    from rideops_api.lens.adapters.base import Secret
    from rideops_api.lens.connections import service
    from rideops_api.settings import LensSettings
    from tests.lens.conftest import TEST_KEYS

    with meta_db.transaction() as conn:
        service.store_secret(conn, service.keyring_for(LensSettings(lens_master_keys=TEST_KEYS)), ws, cid,
                             Secret(password="rotated-elsewhere"))  # fmt: skip
    client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/status/check")
    run_jobs(meta_db)
    latest = client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/status").json()
    assert (
        latest["latest"]["state"] == "auth_failed"
        and latest["latest"]["error_code"] == "CONN_AUTH_FAILED"
    )
    assert latest["connection"]["current_state"] == "auth_failed"
    assert latest["connection"]["last_state_change_at"] is not None
    assert latest["uptime_24h"] == 50.0


def test_rbac_and_tenancy(client, new_client, outbox):
    ws = owner_ws(client, outbox)
    cid = create(client, ws)["id"]
    stranger = new_client()
    signup(stranger)
    for method, path in [("get", "/connections"), ("get", f"/connections/{cid}"), ("get", f"/connections/{cid}/status"),
                         ("post", f"/connections/{cid}/pause"), ("delete", f"/connections/{cid}"),
                         ("post", "/connections/test"), ("get", "/status/overview")]:  # fmt: skip
        kwargs = {"json": {"url": TARGET_URL}} if path.endswith("/test") else {}
        r = getattr(stranger, method)(f"/api/v1/workspaces/{ws}{path}", **kwargs)
        assert r.status_code == 404, (path, r.status_code)
    viewer = new_client()
    vme = signup(viewer, unique_email("viewer"))
    client.post(
        f"/api/v1/workspaces/{ws}/invitations", json={"email": vme["email"], "role": "viewer"}
    )
    viewer.post(f"/api/v1/invitations/{_token(outbox, vme['email'])}/accept")
    assert viewer.get(f"/api/v1/workspaces/{ws}/connections/{cid}/status").status_code == 200
    assert (
        viewer.post(
            f"/api/v1/workspaces/{ws}/connections/test", json={"url": TARGET_URL}
        ).status_code
        == 403
    )
    assert viewer.post(f"/api/v1/workspaces/{ws}/connections/{cid}/pause").status_code == 403
    editor = new_client()
    eme = signup(editor, unique_email("editor"))
    client.post(
        f"/api/v1/workspaces/{ws}/invitations", json={"email": eme["email"], "role": "editor"}
    )
    editor.post(f"/api/v1/invitations/{_token(outbox, eme['email'])}/accept")
    assert editor.post(f"/api/v1/workspaces/{ws}/connections/{cid}/pause").status_code == 200
    assert (
        editor.delete(f"/api/v1/workspaces/{ws}/connections/{cid}").status_code == 403
    )  # admin only


def test_row_level_security_blocks_cross_tenant_reads(client, new_client, outbox, lens_conninfo):
    """FR-04 defence in depth: even a query with no WHERE clause sees only the current workspace."""
    ws_a = owner_ws(client, outbox)
    create(client, ws_a)
    other = new_client()
    ws_b = owner_ws(other, outbox)
    create(other, ws_b, name="B's DB")
    role = f"lens_app_{uuid.uuid4().hex[:6]}"
    with psycopg.connect(lens_conninfo, autocommit=True) as admin:
        admin.execute(f"CREATE ROLE {role} NOSUPERUSER NOBYPASSRLS")
        admin.execute(f"GRANT USAGE ON SCHEMA app TO {role}")
        admin.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA app TO {role}")
    try:
        with psycopg.connect(lens_conninfo) as conn:
            conn.execute(f"SET ROLE {role}")
            conn.execute("SELECT set_config('app.workspace_id', %s, false)", (ws_a,))
            seen = {
                r[0]
                for r in conn.execute("SELECT DISTINCT workspace_id::text FROM app.connections")
            }
            assert seen == {ws_a}
            snaps = conn.execute(
                "SELECT count(*) FROM app.connection_tests WHERE workspace_id = %s", (ws_b,)
            ).fetchone()
            assert snaps[0] == 0
    finally:
        with psycopg.connect(lens_conninfo, autocommit=True) as admin:
            admin.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA app FROM {role}")
            admin.execute(f"REVOKE USAGE ON SCHEMA app FROM {role}")
            admin.execute(f"DROP ROLE {role}")


def test_demo_connection_seeded_on_signup(meta_db, outbox, lens_settings, lens_conninfo):
    demo_url = f"postgresql://{PG.user}:{PG.password.get_secret_value()}@localhost:{PG.port}/{TEST_DB}?sslmode={PG_SSLMODE}"
    settings = lens_settings.model_copy(
        update={"rideops_demo_url": __import__("pydantic").SecretStr(demo_url)}
    )
    app = create_app(db=meta_db, meta_db=meta_db, lens_settings=settings, email_sender=outbox)
    with TestClient(app, base_url="https://testserver") as c:
        me = signup(c)
        ws = me["current_workspace_id"]
        conns = c.get(f"/api/v1/workspaces/{ws}/connections").json()
        assert len(conns) == 1 and conns[0]["is_demo"] and conns[0]["environment"] == "demo"
        assert PG.password.get_secret_value() not in str(conns)
        Worker(meta_db, settings=settings).drain(50)
        st = c.get(f"/api/v1/workspaces/{ws}/connections/{conns[0]['id']}/status").json()
        # Seeding queues a first check, so the demo never sits at "unknown / never checked".
        assert st["latest"] is not None and st["latest"]["reachable"]
        assert st["connection"]["current_state"] in ("healthy", "degraded")
        # A second workspace gets its own demo; re-seeding is idempotent.
        ws2 = c.post("/api/v1/workspaces", json={"name": "Second"}).json()["id"]
        assert len(c.get(f"/api/v1/workspaces/{ws2}/connections").json()) == 1


def test_meta_engines(client):
    signup(client)
    body = client.get("/api/v1/meta/engines").json()
    pg = next(e for e in body["engines"] if e["engine"] == "postgres")
    assert pg["available"] and "status_basic" in pg["capabilities"]
    available = {e["engine"]: e["available"] for e in body["engines"]}
    assert available == {
        "postgres": True,
        "mysql": True,
        "mssql": True,
        "sqlite": True,
        "mongodb": True,
    }
