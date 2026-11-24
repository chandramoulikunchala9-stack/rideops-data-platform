"""Lens tests run against a fresh database `rideops_lens_test` migrated to head."""

import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from rideops.config import PostgresSettings
from rideops_api import migrate
from rideops_api.auth import passwords
from rideops_api.db import Database
from rideops_api.main import create_app
from rideops_api.notifications.email import MemoryEmailSender
from rideops_api.settings import LensSettings

TEST_DB = "rideops_lens_test"
PASSWORD = "correct-horse-battery-1"


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/lens" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.postgres)


@pytest.fixture(scope="session")
def lens_conninfo() -> str:
    try:
        base = PostgresSettings()
        with psycopg.connect(base.conninfo(), autocommit=True) as admin:
            admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
            admin.execute(f"CREATE DATABASE {TEST_DB}")
    except (psycopg.OperationalError, ValueError) as exc:
        pytest.skip(f"PostgreSQL not available: {exc}")
    conninfo = base.conninfo(TEST_DB)
    migrate.upgrade(conninfo)
    passwords.configure(time_cost=1, memory_cost=8 * 1024)  # fast hashing for tests only
    return conninfo


@pytest.fixture(scope="session")
def meta_db(lens_conninfo: str):
    db = Database(conninfo=lens_conninfo, max_size=12)
    db.open()
    yield db
    db.close()


@pytest.fixture(scope="session")
def outbox() -> MemoryEmailSender:
    return MemoryEmailSender()


@pytest.fixture(scope="session")
def lens_settings() -> LensSettings:
    return LensSettings(lens_enabled=True, lens_role="api", session_cookie_secure=True,
                        email_backend="memory", internal_token="tick-secret",
                        public_app_url="https://lens.test")  # fmt: skip


@pytest.fixture(scope="session")
def app(meta_db, outbox, lens_settings):
    return create_app(db=meta_db, meta_db=meta_db, lens_settings=lens_settings, email_sender=outbox)


@pytest.fixture
def client(app):
    """A fresh browser (own cookie jar). https so Secure cookies are sent back."""
    with TestClient(app, base_url="https://testserver") as c:
        yield c


@pytest.fixture
def new_client(app):
    clients = []

    def make() -> TestClient:
        c = TestClient(app, base_url="https://testserver")
        c.__enter__()
        clients.append(c)
        return c

    yield make
    for c in clients:
        c.__exit__(None, None, None)


def unique_email(prefix: str = "user") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}@example.com"


def signup(client: TestClient, email: str | None = None, name: str = "Test User") -> dict:
    email = email or unique_email()
    r = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": PASSWORD, "display_name": name}
    )
    assert r.status_code == 201, r.text
    body = r.json()
    client.headers["X-CSRF-Token"] = body["csrf_token"]
    body["email"] = email
    return body


def sql(conninfo: str, query: str, *params):
    with psycopg.connect(conninfo, autocommit=True, row_factory=dict_row) as conn:
        cur = conn.execute(query, params or None)
        return cur.fetchall() if cur.description else []
