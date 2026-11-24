"""Audit log writer (FR-06). Metadata must never contain secrets."""

import ipaddress
import json
from typing import Any

import psycopg
from fastapi import Request


def client_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    try:  # TestClient reports "testclient", which is not an inet value
        return str(ipaddress.ip_address(request.client.host))
    except ValueError:
        return None


def record(
    conn: psycopg.Connection,
    action: str,
    *,
    request: Request | None = None,
    workspace_id: Any = None,
    actor_user_id: Any = None,
    target_type: str | None = None,
    target_id: Any = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """INSERT INTO app.audit_log (workspace_id, actor_user_id, action, target_type, target_id,
                                      ip, user_agent, request_id, metadata)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            workspace_id,
            actor_user_id,
            action,
            target_type,
            str(target_id) if target_id is not None else None,
            client_ip(request),
            request.headers.get("user-agent") if request else None,
            getattr(request.state, "request_id", None) if request else None,
            json.dumps(metadata or {}, default=str),
        ),
    )
