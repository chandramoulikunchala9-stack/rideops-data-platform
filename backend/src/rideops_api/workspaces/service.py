"""Workspace data access. Every query filters by workspace id (FR-04)."""

import re
import secrets
from typing import Any

import psycopg

PERSONAL_SUFFIX = "'s workspace"


def slugify(text: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "workspace"
    return f"{base}-{secrets.token_hex(3)}"


def create(conn: psycopg.Connection, owner_id: Any, name: str) -> dict:
    ws = conn.execute(
        "INSERT INTO app.workspaces (name, slug) VALUES (%s, %s) RETURNING *",
        (name, slugify(name)),
    ).fetchone()
    conn.execute(
        "INSERT INTO app.memberships (workspace_id, user_id, role) VALUES (%s, %s, 'owner')",
        (ws["id"], owner_id),
    )
    return {**ws, "role": "owner"}


def membership(conn: psycopg.Connection, workspace_id: Any, user_id: Any) -> dict | None:
    """The caller's workspace + role, or None if not a member (or the workspace is deleted)."""
    return conn.execute(
        """SELECT w.*, m.role::text AS role
             FROM app.memberships m JOIN app.workspaces w ON w.id = m.workspace_id
            WHERE m.workspace_id = %s AND m.user_id = %s AND w.deleted_at IS NULL""",
        (workspace_id, user_id),
    ).fetchone()


def list_for_user(conn: psycopg.Connection, user_id: Any) -> list[dict]:
    return conn.execute(
        """SELECT w.*, m.role::text AS role
             FROM app.memberships m JOIN app.workspaces w ON w.id = m.workspace_id
            WHERE m.user_id = %s AND w.deleted_at IS NULL
            ORDER BY (m.role = 'owner') DESC, w.created_at""",
        (user_id,),
    ).fetchall()


def owner_count(conn: psycopg.Connection, workspace_id: Any) -> int:
    return conn.execute(
        "SELECT count(*) AS n FROM app.memberships WHERE workspace_id = %s AND role = 'owner'",
        (workspace_id,),
    ).fetchone()["n"]
