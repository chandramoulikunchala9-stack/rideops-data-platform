"""Phase 15: log redaction, security headers, generic rate limit."""

import logging

from fastapi.testclient import TestClient

from rideops_api import hardening
from rideops_api.main import create_app
from tests.lens.conftest import signup


def test_redaction_masks_credentials():
    samples = {
        "postgresql://lens_ro:hunter2@db.example.com/shop": "postgresql://lens_ro:***@db.example.com/shop",
        "host=x password=s3cret user=y": "host=x password=*** user=y",
        '{"password": "abc", "token": "t0k"}': '{"password": "***", "token": "***"}',
        "Authorization: Bearer abc.def": "Authorization: ***",
        "cookie: lens_session=abc; lens_csrf=def": "cookie: ***",
    }
    for raw, expected in samples.items():
        assert hardening.redact(raw) == expected


def test_redacting_filter_on_records(caplog):
    logger = logging.getLogger("lens.test")
    record = logger.makeRecord("lens.test", logging.ERROR, __file__, 1, "connect failed: %s",
                               ("mysql://root:RootPass-123@h/db",), None)  # fmt: skip
    assert hardening.RedactingFilter().filter(record)
    assert "RootPass-123" not in record.getMessage() and "***" in record.getMessage()


def test_security_headers(client):
    r = client.get("/api/v1/health")
    assert (
        r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    )
    assert r.headers["referrer-policy"] == "same-origin"
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert r.headers["strict-transport-security"].startswith("max-age=")
    assert (
        "content-security-policy" not in client.get("/docs").headers
    )  # Swagger UI needs its scripts


def test_limiter_unit():
    lim = hardening.SlidingWindowLimiter(3, 60)
    assert [lim.hit("k", now=t)[0] for t in (0, 1, 2, 3)] == [True, True, True, False]
    assert lim.hit("k", now=3)[1] >= 1
    assert lim.hit("k", now=61)[0] is True  # the window slid past the first hit
    assert lim.hit("other", now=3)[0] is True


def test_generic_rate_limit(meta_db, outbox, lens_settings):
    settings = lens_settings.model_copy(update={"rate_limit_per_minute": 5})
    app = create_app(db=meta_db, meta_db=meta_db, lens_settings=settings, email_sender=outbox)
    with TestClient(app, base_url="https://testserver") as c:
        signup(c)
        codes = [c.get("/api/v1/auth/me").status_code for _ in range(6)]
        assert codes[-1] == 429 and 200 in codes
        r = c.get("/api/v1/auth/me")
        assert r.json()["error"]["code"] == "RATE_LIMITED" and int(r.headers["retry-after"]) >= 1
        assert c.get("/api/v1/health").status_code in (200, 503)  # health is never limited
