"""`require(action)`: resolve the {ws} path parameter to the caller's membership and check
the role. Non-members get 404 (no existence leak); members without the role get 403."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from fastapi import Depends, Request

from rideops_api.auth.deps import MetaDbDep, SessionDep
from rideops_api.auth.sessions import Session
from rideops_api.errors import ApiError
from rideops_api.workspaces import rbac, service


@dataclass(frozen=True)
class WorkspaceContext:
    session: Session
    workspace: dict[str, Any]
    role: str

    @property
    def workspace_id(self) -> UUID:
        return self.workspace["id"]

    @property
    def user_id(self) -> UUID:
        return self.session.user_id

    def can(self, action: str) -> bool:
        return rbac.allows(self.role, action)


def require(action: str) -> Callable[..., WorkspaceContext]:
    if action not in rbac.PERMISSIONS:
        raise ValueError(f"Unknown action {action!r}")

    def dependency(
        ws: UUID, request: Request, session: SessionDep, db: MetaDbDep
    ) -> WorkspaceContext:
        with db.pool.connection() as conn:
            row = service.membership(conn, ws, session.user_id)
        if row is None:
            raise ApiError(404, "NOT_FOUND", "Workspace not found.")
        if not rbac.allows(row["role"], action):
            raise ApiError(403, "FORBIDDEN", "Your role can't do this.",
                           {"required": rbac.PERMISSIONS[action], "role": row["role"]})  # fmt: skip
        request.state.workspace_id = ws
        return WorkspaceContext(session=session, workspace=row, role=row["role"])

    return Depends(dependency)
