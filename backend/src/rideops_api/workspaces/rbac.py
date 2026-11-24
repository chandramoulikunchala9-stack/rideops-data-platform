"""Roles and the permission matrix (FR-05, §18.3)."""

from typing import Literal

Role = Literal["owner", "admin", "editor", "viewer"]
RANK: dict[str, int] = {"viewer": 0, "editor": 1, "admin": 2, "owner": 3}

# Minimum role per action. Later phases add their actions here, so one table drives
# both the dependency checks and the RBAC tests.
PERMISSIONS: dict[str, Role] = {
    "workspace.view": "viewer",
    "members.view": "viewer",
    "connection.view": "viewer",
    "connection.edit": "editor",
    "query.run": "editor",
    "pii.reveal": "editor",
    "dashboard.edit": "editor",
    "alert_rule.edit": "editor",
    "connection.rotate_secret": "admin",
    "connection.delete": "admin",
    "workspace.edit": "admin",
    "members.manage": "admin",
    "channels.manage": "admin",
    "audit.view": "admin",
    "workspace.security": "owner",
    "workspace.delete": "owner",
    "workspace.transfer": "owner",
}


def allows(role: str, action: str) -> bool:
    return RANK[role] >= RANK[PERMISSIONS[action]]
