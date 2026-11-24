"""Phase 14: alert rules, evaluation, dedup/auto-resolve, signed webhooks with retries, incidents."""

import hashlib
import hmac
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from rideops_api.jobs.worker import Worker
from rideops_api.lens.adapters.base import Secret
from rideops_api.lens.alerts import conditions
from rideops_api.lens.connections import service
from rideops_api.lens.status.health import check_connection
from rideops_api.notifications.email import make_sender
from rideops_api.settings import LensSettings
from tests.lens.conftest import TEST_KEYS, sql
from tests.lens.test_connections import SECRET, create, owner_ws, ws_settings

SETTINGS = LensSettings(lens_master_keys=TEST_KEYS, email_backend="memory")


class Hook:
    """A tiny local webhook receiver. `fail_first` answers HTTP 500 that many times first."""

    def __init__(self, fail_first: int = 0) -> None:
        self.requests: list[dict] = []
        self.fail_first = fail_first
        hook = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers["Content-Length"]))
                hook.requests.append({"headers": dict(self.headers), "body": body})
                code = 500 if hook.fail_first > 0 else 200
                hook.fail_first -= 1
                self.send_response(code)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def hook():
    h = Hook()
    yield h
    h.close()


def drain(meta_db) -> int:
    return Worker(meta_db, settings=SETTINGS).drain(200)


def break_secret(meta_db, ws, cid, password="rotated-elsewhere"):
    with meta_db.transaction() as conn:
        service.store_secret(
            conn, service.keyring_for(SETTINGS), ws, cid, Secret(password=password)
        )


def check(meta_db, cid, times=1):
    for _ in range(times):
        check_connection(meta_db, cid, SETTINGS)
        drain(meta_db)


@pytest.fixture
def setup(client, outbox, meta_db, hook):
    ws = owner_ws(client, outbox)
    settings = ws_settings(egress_allowlist=["localhost", "127.0.0.1"], allowed_ports=[hook.port])
    r = client.patch(f"/api/v1/workspaces/{ws}", json={"settings": settings})
    assert r.status_code == 200
    cid = create(client, ws, name="Orders DB")["id"]
    drain(meta_db)
    return ws, cid


# ---------------------------------------------------------------------- unit


def ctx(snaps=(), **kw):
    return conditions.EvalContext(connection={"name": "db"}, snapshots=list(snaps), **kw)


def test_conditions():
    down = {"state": "unreachable", "error_code": "CONN_TIMEOUT", "latency_ms": None, "metrics": {}}
    ok = {
        "state": "healthy",
        "error_code": None,
        "latency_ms": 20,
        "metrics": {"connections.used_pct": 95},
    }
    assert conditions.status({"for_checks": 2}, ctx([down, down])).breached is True
    assert conditions.status({"for_checks": 2}, ctx([down, ok])).breached is False
    assert conditions.status({"for_checks": 2}, ctx([down])).breached is None  # not enough data
    assert (
        conditions.metric(
            {"metric": "connections.used_pct", "op": ">", "value": 90}, ctx([ok])
        ).breached
        is True
    )
    assert (
        conditions.metric(
            {"metric": "latency_ms", "op": ">", "value": 2000, "for_checks": 2}, ctx([ok, ok])
        ).breached
        is False
    )
    now = datetime.now(timezone.utc)
    stats = {"s.t": [{"taken_at": now, "row_estimate": 600, "freshness_max": now - timedelta(hours=5)},
                     {"taken_at": now - timedelta(days=2), "row_estimate": 1000, "freshness_max": None}]}  # fmt: skip
    assert (
        conditions.freshness(
            {"freshness_table": "s.t", "max_lag_minutes": 120}, ctx(table_stats=stats)
        ).breached
        is True
    )
    drop = conditions.row_drop(
        {"row_drop_table": "s.t", "drop_pct": 30, "window": "1d"}, ctx(table_stats=stats)
    )
    assert drop.breached is True and drop.value == 40.0
    change = [{"change_type": "column_dropped", "object_ref": "s.t.c", "detected_at": now}]
    assert conditions.schema_change({}, ctx(schema_changes=change)).breached is True
    assert conditions.privilege(
        {}, ctx(privilege_findings=[{"privilege": "INSERT", "severity": "warning"}])
    ).breached
    assert conditions.validate_condition(
        "metric", {"metric": "x"}
    ) and conditions.validate_condition("bogus", {})
    assert conditions.validate_condition("metric", {"metric": "x", "value": 1}) is None


# ----------------------------------------------------------------- end to end


def test_default_rule_pack(client, setup):
    ws, cid = setup
    rules = client.get(f"/api/v1/workspaces/{ws}/alert-rules", params={"connection_id": cid}).json()
    assert {r["name"] for r in rules if r["is_default"]} == {
        "Database down",
        "Latency above 2 s",
        "Connections above 90%",
    }


def test_fire_notify_dedup_and_auto_resolve(client, setup, meta_db, hook, lens_conninfo):
    ws, cid = setup
    ch = client.post(
        f"/api/v1/workspaces/{ws}/notification-channels",
        json={"kind": "webhook", "name": "Ops hook", "url": f"http://127.0.0.1:{hook.port}/lens"},
    )
    assert ch.status_code == 201, ch.text
    channel, signing_secret = ch.json(), ch.json()["signing_secret"]
    assert "127.0.0.1" not in str(
        client.get(f"/api/v1/workspaces/{ws}/notification-channels").json()[0].get("url", "")
    )
    rule = next(
        r
        for r in client.get(f"/api/v1/workspaces/{ws}/alert-rules").json()
        if r["name"] == "Database down"
    )
    assert (
        client.patch(
            f"/api/v1/workspaces/{ws}/alert-rules/{rule['id']}", json={"channels": [channel["id"]]}
        ).status_code
        == 200
    )

    hook.fail_first = 1  # first delivery fails → the notify job is retried with backoff
    break_secret(meta_db, ws, cid)
    check(meta_db, cid, times=2)
    alerts = client.get(f"/api/v1/workspaces/{ws}/alerts", params={"status": "open"}).json()
    assert (
        len(alerts) == 1
        and alerts[0]["severity"] == "critical"
        and alerts[0]["rule_name"] == "Database down"
    )
    assert alerts[0]["incident_id"]
    queued = sql(
        lens_conninfo,
        "SELECT status, attempts FROM app.jobs WHERE job_type = 'notify' ORDER BY id DESC LIMIT 1",
    )[0]
    assert queued == {"status": "queued", "attempts": 1}
    sql(
        lens_conninfo,
        "UPDATE app.jobs SET run_at = now() WHERE job_type = 'notify' AND status = 'queued'",
    )
    drain(meta_db)
    assert len(hook.requests) == 2  # one failure + one success
    delivered = hook.requests[-1]
    expected = (
        "sha256=" + hmac.new(signing_secret.encode(), delivered["body"], hashlib.sha256).hexdigest()
    )
    assert delivered["headers"]["X-Lens-Signature"] == expected
    payload = json.loads(delivered["body"])
    assert payload["event"] == "alert.firing" and payload["connection"]["name"] == "Orders DB"
    assert SECRET["password"] not in delivered["body"].decode()

    check(meta_db, cid, times=2)  # still broken: same episode, no new event, no new delivery
    assert (
        len(client.get(f"/api/v1/workspaces/{ws}/alerts").json()) == 1 and len(hook.requests) == 2
    )
    notes = client.get("/api/v1/notifications").json()
    assert notes["unread"] >= 1 and notes["items"][0]["title"].startswith("CRITICAL")

    break_secret(meta_db, ws, cid, SECRET["password"])  # recover
    check(meta_db, cid, times=1)
    assert (
        client.get(f"/api/v1/workspaces/{ws}/alerts").json()[0]["status"] == "firing"
    )  # flapping guard
    check(meta_db, cid, times=1)
    alert = client.get(f"/api/v1/workspaces/{ws}/alerts").json()[0]
    assert alert["status"] == "resolved"
    assert json.loads(hook.requests[-1]["body"])["event"] == "alert.resolved"
    incident = client.get(f"/api/v1/workspaces/{ws}/incidents/{alert['incident_id']}").json()
    assert incident["status"] == "resolved" and len(incident["notes"]) == 2
    assert client.post("/api/v1/notifications/read-all").json() == {"unread": 0}


def test_maintenance_window_suppresses(client, setup, meta_db):
    ws, cid = setup
    now = datetime.now(timezone.utc)
    r = client.post(f"/api/v1/workspaces/{ws}/maintenance-windows",
                    json={"connection_id": cid, "starts_at": (now - timedelta(minutes=1)).isoformat(),
                          "ends_at": (now + timedelta(hours=1)).isoformat(), "reason": "upgrade"})  # fmt: skip
    assert r.status_code == 201
    break_secret(meta_db, ws, cid)
    check(meta_db, cid, times=2)
    assert client.get(f"/api/v1/workspaces/{ws}/alerts").json() == []
    assert (
        client.delete(f"/api/v1/workspaces/{ws}/maintenance-windows/{r.json()['id']}").status_code
        == 200
    )
    check(meta_db, cid, times=1)
    assert len(client.get(f"/api/v1/workspaces/{ws}/alerts", params={"status": "open"}).json()) == 1


def test_ack_mute_resolve_and_incident_notes(client, setup, meta_db):
    ws, cid = setup
    break_secret(meta_db, ws, cid)
    check(meta_db, cid, times=2)
    a = client.get(f"/api/v1/workspaces/{ws}/alerts").json()[0]
    assert (
        client.post(f"/api/v1/workspaces/{ws}/alerts/{a['id']}/ack").json()["status"]
        == "acknowledged"
    )
    assert (
        client.post(f"/api/v1/workspaces/{ws}/alerts/{a['id']}/ack").status_code == 404
    )  # already acked
    until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    assert (
        client.post(f"/api/v1/workspaces/{ws}/alerts/{a['id']}/mute", json={"until": until}).json()[
            "status"
        ]
        == "muted"
    )
    inc = a["incident_id"]
    assert (
        client.post(
            f"/api/v1/workspaces/{ws}/incidents/{inc}/notes",
            json={"body": "Investigating the password change."},
        ).status_code
        == 201
    )
    assert (
        client.post(f"/api/v1/workspaces/{ws}/alerts/{a['id']}/resolve").json()["status"]
        == "resolved"
    )
    detail = client.get(f"/api/v1/workspaces/{ws}/incidents/{inc}").json()
    assert detail["status"] == "resolved" and any("password" in n["body"] for n in detail["notes"])
    manual = client.post(
        f"/api/v1/workspaces/{ws}/incidents", json={"title": "Slow reports", "severity": "warning"}
    ).json()
    assert client.patch(
        f"/api/v1/workspaces/{ws}/incidents/{manual['id']}", json={"status": "acknowledged"}
    ).json()["acknowledged_at"]


def test_channel_safety_email_slack_and_rule_validation(client, setup, new_client, outbox):
    ws, cid = setup
    r = client.post(
        f"/api/v1/workspaces/{ws}/notification-channels",
        json={"kind": "webhook", "name": "evil", "url": "https://169.254.169.254/latest/meta-data"},
    )
    assert r.status_code == 403 and r.json()["error"]["code"] == "CONN_BLOCKED_NETWORK"
    r = client.post(f"/api/v1/workspaces/{ws}/notification-channels",
                    json={"kind": "slack", "name": "s", "url": "ftp://hooks.slack.com/x"})  # fmt: skip
    assert r.status_code == 422
    email = client.post(f"/api/v1/workspaces/{ws}/notification-channels",
                        json={"kind": "email", "name": "On-call", "emails": ["oncall@example.com"]}).json()  # fmt: skip
    assert email["summary"] == "1 recipient(s)" and "oncall" not in json.dumps(email)
    from tests.lens.conftest import signup

    rule = client.post(
        f"/api/v1/workspaces/{ws}/alert-rules",
        json={
            "name": "Orders stale",
            "connection_id": cid,
            "rule_type": "freshness",
            "severity": "warning",
            "condition": {"freshness_table": "public.orders", "max_lag_minutes": 60},
            "channels": [email["id"]],
        },
    ).json()
    sender = make_sender("memory")
    before = len(sender.outbox)
    result = client.post(f"/api/v1/workspaces/{ws}/alert-rules/{rule['id']}/test").json()["results"]
    assert result == [{"channel_id": email["id"], "ok": True}] and len(sender.outbox) == before + 1
    assert client.post(f"/api/v1/workspaces/{ws}/alert-rules", json={"name": "bad", "rule_type": "metric",
                       "condition": {"metric": "x"}}).status_code == 422  # fmt: skip
    viewer = new_client()
    signup(viewer)
    assert viewer.get(f"/api/v1/workspaces/{ws}/alerts").status_code == 404
