"""Server-side sessions (FR-02) and single-use email tokens."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from uuid import UUID

import psycopg

from rideops_api.audit import client_ip
from rideops_api.auth.tokens import digest, new_secret

COOKIE_NAME = "lens_session"
CSRF_COOKIE_NAME = "lens_csrf"
TOKEN_TTL = {"verify_email": timedelta(hours=24), "password_reset": timedelta(minutes=30)}


@dataclass(frozen=True)
class Session:
    id_hash: str
    user_id: UUID
    csrf_token: str
    email: str
    display_name: str
    email_verified: bool


def create(
    conn: psycopg.Connection, user_id: Any, *, request=None, absolute_days: int = 7
) -> tuple[str, str]:
    """Return (raw session id for the cookie, csrf token)."""
    raw, csrf = new_secret(32), new_secret(32)
    conn.execute(
        """INSERT INTO app.sessions (id, user_id, csrf_token, ip, user_agent, expires_at)
           VALUES (%s, %s, %s, %s, %s, now() + make_interval(days => %s))""",
        (digest(raw), user_id, csrf, client_ip(request),
         request.headers.get("user-agent") if request else None, absolute_days),
    )  # fmt: skip
    return raw, csrf


def lookup(conn: psycopg.Connection, raw: str, *, idle_hours: int = 12) -> Session | None:
    row = conn.execute(
        """UPDATE app.sessions s
              SET last_seen_at = now()
             FROM app.users u
            WHERE s.id = %s AND u.id = s.user_id AND NOT u.is_disabled
              AND s.expires_at > now()
              AND s.last_seen_at > now() - make_interval(hours => %s)
        RETURNING s.id, s.user_id, s.csrf_token, u.email, u.display_name, u.email_verified""",
        (digest(raw), idle_hours),
    ).fetchone()
    if row is None:
        return None
    return Session(row["id"], row["user_id"], row["csrf_token"], row["email"],
                   row["display_name"], row["email_verified"])  # fmt: skip


def destroy(conn: psycopg.Connection, id_hash: str) -> None:
    conn.execute("DELETE FROM app.sessions WHERE id = %s", (id_hash,))


def destroy_all_for_user(conn: psycopg.Connection, user_id: Any) -> None:
    conn.execute("DELETE FROM app.sessions WHERE user_id = %s", (user_id,))


def issue_user_token(conn: psycopg.Connection, user_id: Any, purpose: str) -> str:
    raw = new_secret(32)
    conn.execute(
        """INSERT INTO app.user_tokens (user_id, purpose, token_hash, expires_at)
           VALUES (%s, %s, %s, now() + %s)""",
        (user_id, purpose, digest(raw), TOKEN_TTL[purpose]),
    )
    return raw


def consume_user_token(conn: psycopg.Connection, raw: str, purpose: str) -> UUID | None:
    """Mark the token used and return its user, or None if unknown/expired/already used."""
    row = conn.execute(
        """UPDATE app.user_tokens SET used_at = now()
            WHERE token_hash = %s AND purpose = %s AND used_at IS NULL AND expires_at > now()
        RETURNING user_id""",
        (digest(raw), purpose),
    ).fetchone()
    return row["user_id"] if row else None
