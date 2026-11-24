"""Phase 14 through the API: SQLite upload lifecycle and a MySQL connection end to end."""

import socket
import sqlite3

import pytest

from rideops_api.jobs.worker import Worker
from rideops_api.settings import LensSettings
from tests.lens.conftest import TEST_KEYS
from tests.lens.test_connections import owner_ws


@pytest.fixture
def uploads_client(app, lens_settings, tmp_path):
    """The shared app, pointed at a temporary uploads directory for this test."""
    from fastapi.testclient import TestClient

    app.state.lens_settings = lens_settings.model_copy(
        update={"uploads_dir": str(tmp_path / "uploads")}
    )
    with TestClient(app, base_url="https://testserver") as c:
        yield c, app.state.lens_settings
    app.state.lens_settings = lens_settings


def _sqlite_bytes(tmp_path) -> bytes:
    path = tmp_path / "shop.sqlite"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE customers (id INTEGER PRIMARY KEY, email TEXT, created_at TEXT)")
    c.executemany(
        "INSERT INTO customers VALUES (?, ?, datetime('now'))",
        [(i, f"u{i}@example.com") for i in range(1, 101)],
    )
    c.commit()
    c.close()
    return path.read_bytes()


def test_sqlite_upload_lifecycle(uploads_client, outbox, meta_db, tmp_path):
    client, settings = uploads_client
    ws = owner_ws(client, outbox, allow_localhost=False)
    up = client.post(
        f"/api/v1/workspaces/{ws}/uploads/sqlite",
        content=_sqlite_bytes(tmp_path),
        headers={"Content-Type": "application/octet-stream", "X-Filename": "../../etc/shop.sqlite"},
    )
    assert up.status_code == 201, up.text
    body = up.json()
    assert body["filename"] == "shop.sqlite" and body["bytes"] > 0  # path parts stripped
    bad = client.post(f"/api/v1/workspaces/{ws}/uploads/sqlite", content=b"not a database at all",
                      headers={"Content-Type": "application/octet-stream"})  # fmt: skip
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "UPLOAD_NOT_SQLITE"

    draft = {"engine": "sqlite", "database": body["upload_id"]}
    t = client.post(f"/api/v1/workspaces/{ws}/connections/test", json=draft).json()
    assert t["ok"], t
    assert [s["stage"] for s in t["stages"]] == [
        "TCP",
        "TLS",
        "AUTH",
        "DB_SELECT",
        "PRIVILEGES",
        "CAPABILITIES",
    ]
    r = client.post(
        f"/api/v1/workspaces/{ws}/connections",
        json={"test_id": t["test_id"], "draft": draft, "name": "Shop file"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["display_url"].startswith("sqlite://")
    Worker(
        meta_db, settings=LensSettings(lens_master_keys=TEST_KEYS, uploads_dir=settings.uploads_dir)
    ).drain(50)
    st = client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/status").json()
    assert st["latest"]["reachable"] and st["connection"]["server_version"].startswith("SQLite")
    cat = client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/catalog").json()
    assert [t["table_name"] for t in cat["tables"]] == ["customers"] and cat["tables"][0][
        "pii_columns"
    ] == ["email"]
    q = client.post(f"/api/v1/workspaces/{ws}/connections/{cid}/queries/run",
                    json={"statement": "select count(*) as n, max(email) as email from customers"}).json()  # fmt: skip
    assert q["rows"] == [[100, "•••"]]
    w = client.post(
        f"/api/v1/workspaces/{ws}/connections/{cid}/queries/run",
        json={"statement": "delete from customers"},
    )
    assert w.status_code == 422
    # Another workspace can't point a connection at this workspace's upload.
    from tests.lens.conftest import signup

    other_ws = signup(client)["current_workspace_id"]  # same browser: a second account's workspace
    t2 = client.post(f"/api/v1/workspaces/{other_ws}/connections/test", json=draft).json()
    assert not t2["ok"] and t2["stages"][-1]["code"] == "CONN_DB_NOT_FOUND"


def _mysql_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 3307), timeout=1):
            return True
    except OSError:
        return False


def _seed_mysql_shop() -> None:
    """Create the `shop` tables this test reads, so it works against a fresh container (CI)."""
    import pymysql

    c = pymysql.connect(host="127.0.0.1", port=3307, user="root", password="RootPass-123",
                        autocommit=True)  # fmt: skip
    with c.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS shop")
        cur.execute("DROP TABLE IF EXISTS shop.orders, shop.customers")
        cur.execute("CREATE TABLE shop.customers (id INT PRIMARY KEY, email VARCHAR(120))")
        cur.execute("CREATE TABLE shop.orders (id INT PRIMARY KEY, customer_id INT, status VARCHAR(20), "
                    "FOREIGN KEY (customer_id) REFERENCES shop.customers(id))")  # fmt: skip
        cur.execute("INSERT INTO shop.customers VALUES (1, 'a@example.com'), (2, 'b@example.com')")
        cur.execute(
            "INSERT INTO shop.orders VALUES (1, 1, 'paid'), (2, 1, 'paid'), (3, 2, 'refunded')"
        )
    c.close()


@pytest.mark.skipif(not _mysql_up(), reason="MySQL test container not running on 127.0.0.1:3307")
def test_mysql_connection_end_to_end(client, outbox, meta_db):
    _seed_mysql_shop()
    ws = owner_ws(client, outbox)
    client.patch(
        f"/api/v1/workspaces/{ws}",
        json={"settings": {"egress_allowlist": ["localhost"], "allowed_ports": [3307]}},
    )
    url = "mysql://root@localhost:3307/shop?ssl_mode=REQUIRED"
    secret = {"password": "RootPass-123"}
    t = client.post(
        f"/api/v1/workspaces/{ws}/connections/test", json={"url": url, "secret": secret}
    ).json()
    assert t["ok"] and t["server_version"].startswith("MySQL"), t
    assert (
        "GRANT SELECT, SHOW VIEW" in t["grant_script"]
    )  # root can write → MySQL-specific fix shown
    r = client.post(f"/api/v1/workspaces/{ws}/connections", json={"test_id": t["test_id"],
                    "draft": {"url": url, "secret": secret}, "name": "Shop MySQL"})  # fmt: skip
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    Worker(meta_db, settings=LensSettings(lens_master_keys=TEST_KEYS)).drain(50)
    st = client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/status").json()
    assert st["latest"]["reachable"] and st["latest"]["metrics"]["connections.max"] > 0
    names = {
        t["table_name"]
        for t in client.get(f"/api/v1/workspaces/{ws}/connections/{cid}/catalog").json()["tables"]
    }
    assert {"customers", "orders"} <= names
    q = client.post(
        f"/api/v1/workspaces/{ws}/connections/{cid}/queries/run",
        json={
            "statement": "select status, count(*) as n from orders group by status order by status"
        },
    ).json()
    assert q["row_count"] == 2
    bad = client.post(
        f"/api/v1/workspaces/{ws}/connections/{cid}/queries/run",
        json={"statement": "drop table orders"},
    )
    assert bad.status_code == 422
