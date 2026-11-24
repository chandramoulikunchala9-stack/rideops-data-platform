import re

from tests.lens.conftest import PASSWORD, signup, sql, unique_email


def _token(outbox, to: str) -> str:
    mail = next(m for m in reversed(outbox.outbox) if m.to == to)
    return re.search(r"token=([\w-]+)", mail.body).group(1)


def test_signup_creates_user_workspace_session(client, outbox, lens_conninfo):
    me = signup(client, name="Asha")
    assert me["user"]["display_name"] == "Asha"
    assert me["user"]["email_verified"] is False
    assert len(me["memberships"]) == 1 and me["memberships"][0]["role"] == "owner"
    assert me["current_workspace_id"] == me["memberships"][0]["workspace_id"]
    cookie = client.cookies.get("lens_session")
    assert cookie
    # The raw session id is never stored, only its hash.
    assert not sql(lens_conninfo, "SELECT 1 FROM app.sessions WHERE id = %s", cookie)
    # Password is an Argon2id hash.
    row = sql(lens_conninfo, "SELECT password_hash FROM app.users WHERE email = %s", me["email"])[0]
    assert row["password_hash"].startswith("$argon2id$")
    assert client.get("/api/v1/auth/me").json()["user"]["email"] == me["email"]
    assert any(m.to == me["email"] for m in outbox.outbox)


def test_session_cookie_flags(client):
    email = unique_email()
    r = client.post(
        "/api/v1/auth/signup", json={"email": email, "password": PASSWORD, "display_name": "X"}
    )
    set_cookie = [v for k, v in r.headers.multi_items() if k == "set-cookie"]
    session = next(c for c in set_cookie if c.startswith("lens_session="))
    assert "HttpOnly" in session and "Secure" in session and "samesite=lax" in session.lower()
    csrf = next(c for c in set_cookie if c.startswith("lens_csrf="))
    assert "HttpOnly" not in csrf


def test_weak_and_breached_passwords_rejected(client):
    for pw, msg in [("short", "12 characters"), ("password1234", "breached")]:
        r = client.post(
            "/api/v1/auth/signup",
            json={"email": unique_email(), "password": pw, "display_name": "X"},
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "PASSWORD_WEAK"
        assert msg in r.json()["error"]["message"]


def test_duplicate_email(client, new_client):
    me = signup(client)
    r = new_client().post("/api/v1/auth/signup",
                          json={"email": me["email"].upper(), "password": PASSWORD, "display_name": "X"})  # fmt: skip
    assert r.status_code == 409 and r.json()["error"]["code"] == "EMAIL_TAKEN"


def test_verify_email_single_use(client, outbox):
    me = signup(client)
    token = _token(outbox, me["email"])
    assert client.post("/api/v1/auth/verify-email", json={"token": token}).status_code == 200
    assert client.get("/api/v1/auth/me").json()["user"]["email_verified"] is True
    r = client.post("/api/v1/auth/verify-email", json={"token": token})
    assert r.status_code == 400 and r.json()["error"]["code"] == "TOKEN_INVALID"


def test_csrf_required_for_mutations(client):
    signup(client)
    csrf = client.headers.pop("X-CSRF-Token")
    r = client.post("/api/v1/workspaces", json={"name": "No CSRF"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = client.post(
        "/api/v1/workspaces", json={"name": "Wrong"}, headers={"X-CSRF-Token": "x" * 43}
    )
    assert r.status_code == 403
    assert client.get("/api/v1/auth/me").status_code == 200  # safe methods need no token
    assert (
        client.post(
            "/api/v1/workspaces", json={"name": "Ok"}, headers={"X-CSRF-Token": csrf}
        ).status_code
        == 201
    )


def test_unauthenticated(client):
    r = client.get("/api/v1/auth/me")
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHENTICATED"
    client.cookies.set("lens_session", "forged-session-id", domain="testserver")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_logout_and_rotation(client, new_client, lens_conninfo):
    me = signup(client)
    first = client.cookies.get("lens_session")
    browser = new_client()
    r = browser.post(
        "/api/v1/auth/login", json={"email": me["email"], "password": "wrong-password-123"}
    )
    assert (
        r.status_code == 401 and r.json()["error"]["message"] == "Email or password is incorrect."
    )
    r = browser.post(
        "/api/v1/auth/login", json={"email": unique_email(), "password": "whatever-password"}
    )
    assert (
        r.status_code == 401 and r.json()["error"]["message"] == "Email or password is incorrect."
    )
    r = browser.post("/api/v1/auth/login", json={"email": me["email"], "password": PASSWORD})
    assert r.status_code == 200
    second = browser.cookies.get("lens_session")
    assert second and second != first
    browser.headers["X-CSRF-Token"] = r.json()["csrf_token"]
    assert browser.post("/api/v1/auth/logout").status_code == 200
    browser.cookies.set("lens_session", second, domain="testserver")
    assert browser.get("/api/v1/auth/me").status_code == 401  # destroyed server-side
    assert client.get("/api/v1/auth/me").status_code == 200  # other session unaffected
    actions = {r["action"] for r in sql(lens_conninfo,
               "SELECT a.action FROM app.audit_log a JOIN app.users u ON u.id = a.actor_user_id WHERE u.email = %s",
               me["email"])}  # fmt: skip
    assert {"user.signup", "user.login_failed", "user.login", "user.logout"} <= actions


def test_login_lockout(client, new_client):
    me = signup(client)
    browser = new_client()
    for _ in range(10):
        assert (
            browser.post(
                "/api/v1/auth/login", json={"email": me["email"], "password": "nope-nope-nope"}
            ).status_code
            == 401
        )
    r = browser.post("/api/v1/auth/login", json={"email": me["email"], "password": PASSWORD})
    assert r.status_code == 429 and r.json()["error"]["code"] == "RATE_LIMITED"


def test_session_idle_and_absolute_expiry(client, lens_conninfo):
    signup(client)
    cookie = client.cookies.get("lens_session")
    import hashlib

    h = hashlib.sha256(cookie.encode()).hexdigest()
    sql(
        lens_conninfo,
        "UPDATE app.sessions SET last_seen_at = now() - interval '13 hours' WHERE id = %s RETURNING 1",
        h,
    )
    assert client.get("/api/v1/auth/me").status_code == 401
    sql(lens_conninfo, "UPDATE app.sessions SET last_seen_at = now(), expires_at = now() - interval '1 second' "
                       "WHERE id = %s RETURNING 1", h)  # fmt: skip
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_reset_flow(client, new_client, outbox):
    me = signup(client)
    other = new_client()
    unknown = other.post("/api/v1/auth/password/forgot", json={"email": unique_email()})
    assert unknown.status_code == 202
    sent_before = len(outbox.outbox)
    assert (
        other.post("/api/v1/auth/password/forgot", json={"email": me["email"]}).status_code == 202
    )
    assert len(outbox.outbox) == sent_before + 1
    token = _token(outbox, me["email"])
    r = other.post("/api/v1/auth/password/reset", json={"token": token, "password": "short"})
    assert r.status_code == 422
    new_pw = "a-brand-new-passphrase-42"
    assert (
        other.post(
            "/api/v1/auth/password/reset", json={"token": token, "password": new_pw}
        ).status_code
        == 200
    )
    assert client.get("/api/v1/auth/me").status_code == 401  # all sessions revoked
    assert (
        other.post(
            "/api/v1/auth/password/reset", json={"token": token, "password": new_pw}
        ).status_code
        == 400
    )
    assert (
        other.post(
            "/api/v1/auth/login", json={"email": me["email"], "password": PASSWORD}
        ).status_code
        == 401
    )
    r = other.post("/api/v1/auth/login", json={"email": me["email"], "password": new_pw})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True
