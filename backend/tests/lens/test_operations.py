# ruff: noqa: E501 - SQL fixtures read best on one line
"""Phase 15: quotas, roll-ups + retention, /metrics, KEK rotation."""

import base64
import os

from rideops_api.jobs import queue
from rideops_api.jobs.worker import Worker
from rideops_api.lens.connections import service
from rideops_api.lens.security import rotation
from rideops_api.lens.status import rollups
from rideops_api.settings import LensSettings
from tests.lens.conftest import TEST_KEYS, sql
from tests.lens.test_connections import SECRET, TARGET_URL, create, owner_ws, test_draft

SETTINGS = LensSettings(lens_master_keys=TEST_KEYS)


def test_free_plan_quotas(client, outbox, app, lens_settings):
    ws = owner_ws(client, outbox)
    app.state.lens_settings = lens_settings.model_copy(update={"free_plan_max_connections": 1,
                                                               "free_plan_min_interval_s": 300})  # fmt: skip
    try:
        t = test_draft(client, ws)
        body = {
            "test_id": t["test_id"],
            "draft": {"url": TARGET_URL, "secret": SECRET},
            "name": "Fast",
            "check_interval_s": 60,
        }
        r = client.post(f"/api/v1/workspaces/{ws}/connections", json=body)
        assert r.status_code == 403 and r.json()["error"]["code"] == "QUOTA_EXCEEDED"
        cid = client.post(
            f"/api/v1/workspaces/{ws}/connections", json={**body, "check_interval_s": 300}
        ).json()["id"]
        r = client.patch(
            f"/api/v1/workspaces/{ws}/connections/{cid}", json={"check_interval_s": 60}
        )
        assert r.status_code == 403 and r.json()["error"]["details"] == {"min_interval_s": 300}
        t2 = test_draft(client, ws)
        r = client.post(f"/api/v1/workspaces/{ws}/connections", json={**body, "test_id": t2["test_id"], "name": "Second",
                                                                       "check_interval_s": 300})  # fmt: skip
        assert r.status_code == 403 and r.json()["error"]["details"] == {"limit": 1}
    finally:
        app.state.lens_settings = lens_settings


def test_rollups_keep_uptime_after_raw_retention(client, outbox, meta_db, lens_conninfo):
    ws, cid = owner_ws(client, outbox), None
    cid = create(client, ws, name="History")["id"]
    Worker(meta_db, settings=SETTINGS).drain(
        20
    )  # the connection's own first checks, before we measure
    # 10 days of hourly checks: day 9 ago was down, everything else healthy.
    sql(
        lens_conninfo,
        """
        INSERT INTO app.status_snapshots (workspace_id, connection_id, taken_at, state, reachable, latency_ms, health_score)
        SELECT %s, %s, h, CASE WHEN h < now() - interval '9 days' + interval '1 hour'
                                AND h >= now() - interval '9 days' THEN 'down' ELSE 'healthy' END::app.conn_state,
               true, 20, 95
          FROM generate_series(now() - interval '10 days', now() - interval '1 hour', interval '1 hour') h""",
        ws,
        cid,
    )
    before = client.get(
        f"/api/v1/workspaces/{ws}/connections/{cid}/uptime", params={"window": "30d"}
    ).json()
    rollups.rollup(
        meta_db, LensSettings(lens_master_keys=TEST_KEYS, retention_raw_days=30)
    )  # roll everything up
    with meta_db.pool.connection() as conn:
        queue.enqueue(conn, "retention")
    Worker(meta_db, settings=SETTINGS).drain(20)  # raw window back to 7 days: old snapshots deleted
    left = sql(lens_conninfo, "SELECT min(taken_at) > now() - interval '8 days' AS ok FROM app.status_snapshots "
                              "WHERE connection_id = %s", cid)[0]["ok"]  # fmt: skip
    assert left
    after = client.get(
        f"/api/v1/workspaces/{ws}/connections/{cid}/uptime", params={"window": "30d"}
    ).json()
    assert after["checks"] == before["checks"] and after["uptime_pct"] == before["uptime_pct"] < 100
    from datetime import datetime, timedelta, timezone

    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    hist = client.get(
        f"/api/v1/workspaces/{ws}/connections/{cid}/status/history",
        params={"grain": "day", "from": since},
    ).json()
    assert sum(p["total_checks"] for p in hist) == before["checks"]


def test_metrics_endpoint(client, outbox):
    ws = owner_ws(client, outbox)
    client.post(
        f"/api/v1/workspaces/{ws}/connections/test",
        json={"url": "postgresql://u@169.254.169.254/x"},
    )
    assert client.get("/metrics").status_code == 403
    r = client.get("/metrics", headers={"Authorization": "Bearer tick-secret"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    text = r.text
    for name in ("lens_jobs_queued", "lens_job_oldest_queued_seconds", "lens_connections", "lens_ssrf_blocks_total",
                 "lens_query_rejections_total", "lens_api_requests_total"):  # fmt: skip
        assert f"# TYPE {name} gauge" in text
    ssrf = next(line for line in text.splitlines() if line.startswith("lens_ssrf_blocks_total "))
    assert float(ssrf.split()[1]) >= 1


def test_kek_rotation_rewraps_everything(client, outbox, meta_db, lens_conninfo):
    ws = owner_ws(client, outbox)
    cid = create(client, ws, name="Rotate me")["id"]
    client.post(f"/api/v1/workspaces/{ws}/notification-channels",
                json={"kind": "email", "name": "On-call", "emails": ["oncall@example.com"]})  # fmt: skip
    new_key = base64.b64encode(os.urandom(32)).decode()
    rotated = LensSettings(lens_master_keys=f"{TEST_KEYS},3:{new_key}")
    assert rotation.remaining(meta_db, rotated) >= 2
    while any(rotation.rewrap_all(meta_db, rotated).values()):
        pass
    assert rotation.remaining(meta_db, rotated) == 0
    assert (
        sql(
            lens_conninfo,
            "SELECT kek_version FROM app.connection_secrets WHERE connection_id = %s",
            cid,
        )[0]["kek_version"]
        == 3
    )
    # Old versions can now be retired: only key 3 is needed to read the secret.
    only_new = LensSettings(lens_master_keys=f"3:{new_key}")
    with meta_db.pool.connection() as conn:
        secret = service.load_secret(conn, service.keyring_for(only_new), ws, cid)
    assert secret.password == SECRET["password"]
