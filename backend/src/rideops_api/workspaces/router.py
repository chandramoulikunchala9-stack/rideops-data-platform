"""/workspaces, members, invitations and the audit log (§13.2)."""

import json
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Request, status

from rideops_api import audit
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep, SessionDep
from rideops_api.auth.schemas import (
    AuditEvent,
    AuditPage,
    InvitationIn,
    InvitationOut,
    MemberOut,
    MemberPatch,
    Ok,
    WorkspaceIn,
    WorkspaceOut,
    WorkspacePatch,
)
from rideops_api.auth.tokens import digest, new_secret
from rideops_api.errors import ApiError
from rideops_api.notifications.email import Email
from rideops_api.workspaces import rbac, service
from rideops_api.workspaces.deps import WorkspaceContext, require

router = APIRouter(tags=["workspaces"])

Viewer = Annotated[WorkspaceContext, require("workspace.view")]
Admin = Annotated[WorkspaceContext, require("workspace.edit")]
Owner = Annotated[WorkspaceContext, require("workspace.delete")]
MembersView = Annotated[WorkspaceContext, require("members.view")]
MembersManage = Annotated[WorkspaceContext, require("members.manage")]
AuditView = Annotated[WorkspaceContext, require("audit.view")]

INVITE_TTL_DAYS = 7


def _out(row: dict) -> WorkspaceOut:
    return WorkspaceOut(id=row["id"], name=row["name"], slug=row["slug"], plan=row["plan"],
                        settings=row["settings"], role=row["role"], created_at=row["created_at"])  # fmt: skip


@router.get("/workspaces", response_model=list[WorkspaceOut])
def list_workspaces(session: SessionDep, db: MetaDbDep) -> list[WorkspaceOut]:
    with db.pool.connection() as conn:
        return [_out(r) for r in service.list_for_user(conn, session.user_id)]


@router.post("/workspaces", response_model=WorkspaceOut, status_code=status.HTTP_201_CREATED)
def create_workspace(body: WorkspaceIn, session: SessionDep, request: Request, db: MetaDbDep) -> WorkspaceOut:
    with db.transaction() as conn:
        ws = service.create(conn, session.user_id, body.name.strip())
        audit.record(conn, "workspace.create", request=request, workspace_id=ws["id"],
                     actor_user_id=session.user_id, target_type="workspace", target_id=ws["id"])  # fmt: skip
    return _out(ws)


@router.get("/workspaces/{ws}", response_model=WorkspaceOut)
def get_workspace(ctx: Viewer) -> WorkspaceOut:
    return _out(ctx.workspace)


@router.patch("/workspaces/{ws}", response_model=WorkspaceOut)
def update_workspace(body: WorkspacePatch, ctx: Admin, request: Request, db: MetaDbDep) -> WorkspaceOut:
    settings = None
    if body.settings is not None:
        current = ctx.workspace["settings"]
        new = body.settings.model_dump()
        security_changed = (new["allow_insecure_connections"] != current.get("allow_insecure_connections", False)
                            or new["egress_allowlist"] != current.get("egress_allowlist", []))  # fmt: skip
        if security_changed and not ctx.can("workspace.security"):
            raise ApiError(403, "FORBIDDEN", "Only an owner can change network security settings.",
                           {"required": "owner", "role": ctx.role})  # fmt: skip
        settings = json.dumps(new)
    with db.transaction() as conn:
        conn.execute(
            """UPDATE app.workspaces SET name = COALESCE(%s, name), settings = COALESCE(%s::jsonb, settings)
                WHERE id = %s""",
            (body.name.strip() if body.name else None, settings, ctx.workspace_id),
        )
        audit.record(conn, "workspace.update", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="workspace", target_id=ctx.workspace_id,
                     metadata=body.model_dump(exclude_none=True))  # fmt: skip
        row = service.membership(conn, ctx.workspace_id, ctx.user_id)
    return _out(row)


@router.delete("/workspaces/{ws}", response_model=Ok)
def delete_workspace(ctx: Owner, request: Request, db: MetaDbDep) -> Ok:
    """Soft delete; data is erased by the retention job (GDPR export/erase is v1.x)."""
    with db.transaction() as conn:
        conn.execute("UPDATE app.workspaces SET deleted_at = now() WHERE id = %s", (ctx.workspace_id,))
        audit.record(conn, "workspace.delete", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="workspace", target_id=ctx.workspace_id)  # fmt: skip
    return Ok()


@router.get("/workspaces/{ws}/members", response_model=list[MemberOut])
def list_members(ctx: MembersView, db: MetaDbDep) -> list[MemberOut]:
    with db.pool.connection() as conn:
        rows = conn.execute(
            """SELECT u.id AS user_id, u.email, u.display_name, m.role::text AS role, m.created_at AS joined_at
                 FROM app.memberships m JOIN app.users u ON u.id = m.user_id
                WHERE m.workspace_id = %s ORDER BY m.created_at""",
            (ctx.workspace_id,),
        ).fetchall()
    return [MemberOut(**r) for r in rows]


@router.post("/workspaces/{ws}/invitations", response_model=InvitationOut, status_code=status.HTTP_201_CREATED)
def invite(body: InvitationIn, ctx: MembersManage, request: Request, db: MetaDbDep,
           settings: LensSettingsDep) -> InvitationOut:  # fmt: skip
    if body.role == "owner" and not ctx.can("workspace.transfer"):
        raise ApiError(403, "FORBIDDEN", "Only an owner can invite another owner.")
    raw = new_secret(32)
    with db.transaction() as conn:
        already = conn.execute(
            """SELECT 1 FROM app.memberships m JOIN app.users u ON u.id = m.user_id
                WHERE m.workspace_id = %s AND u.email = %s""",
            (ctx.workspace_id, body.email),
        ).fetchone()
        if already:
            raise ApiError(409, "ALREADY_MEMBER", "This person is already a member.")
        inv = conn.execute(
            """INSERT INTO app.invitations (workspace_id, email, role, token_hash, expires_at, invited_by)
               VALUES (%s, %s, %s, %s, now() + make_interval(days => %s), %s)
               RETURNING id, email, role::text AS role, expires_at""",
            (ctx.workspace_id, body.email, body.role, digest(raw), INVITE_TTL_DAYS, ctx.user_id),
        ).fetchone()
        audit.record(conn, "member.invite", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="invitation", target_id=inv["id"],
                     metadata={"email": body.email, "role": body.role})  # fmt: skip
    link = f"{settings.public_app_url.rstrip('/')}/app/invite?token={raw}"
    request.app.state.email_sender.send(Email(
        to=body.email, subject=f"You're invited to {ctx.workspace['name']} on RideOps Lens",
        body=f"{ctx.session.display_name} invited you as {body.role}:\n{link}\n"))  # fmt: skip
    return InvitationOut(**inv)


@router.post("/invitations/{token}/accept", response_model=WorkspaceOut)
def accept_invitation(token: str, session: SessionDep, request: Request, db: MetaDbDep) -> WorkspaceOut:
    with db.transaction() as conn:
        inv = conn.execute(
            """SELECT i.* FROM app.invitations i JOIN app.workspaces w ON w.id = i.workspace_id
                WHERE i.token_hash = %s AND i.accepted_at IS NULL AND i.expires_at > now()
                  AND w.deleted_at IS NULL
                FOR UPDATE OF i""",
            (digest(token),),
        ).fetchone()
        # Same error for unknown and for someone else's invitation: no enumeration.
        if inv is None or inv["email"].lower() != session.email.lower():
            raise ApiError(404, "NOT_FOUND", "This invitation is invalid, expired, or for another account.")
        conn.execute(
            """INSERT INTO app.memberships (workspace_id, user_id, role) VALUES (%s, %s, %s)
               ON CONFLICT (workspace_id, user_id) DO NOTHING""",
            (inv["workspace_id"], session.user_id, inv["role"]),
        )
        conn.execute("UPDATE app.invitations SET accepted_at = now() WHERE id = %s", (inv["id"],))
        # Accepting a link sent to the address proves ownership of it.
        conn.execute("UPDATE app.users SET email_verified = true WHERE id = %s", (session.user_id,))
        audit.record(conn, "member.join", request=request, workspace_id=inv["workspace_id"],
                     actor_user_id=session.user_id, target_type="invitation", target_id=inv["id"])  # fmt: skip
        row = service.membership(conn, inv["workspace_id"], session.user_id)
    return _out(row)


def _target_role(conn, ctx: WorkspaceContext, user_id: UUID) -> str:
    row = conn.execute(
        "SELECT role::text AS role FROM app.memberships WHERE workspace_id = %s AND user_id = %s FOR UPDATE",
        (ctx.workspace_id, user_id),
    ).fetchone()
    if row is None:
        raise ApiError(404, "NOT_FOUND", "Member not found.")
    return row["role"]


def _guard_owner_change(conn, ctx: WorkspaceContext, current: str, new: str | None) -> None:
    touches_owner = current == "owner" or new == "owner"
    if touches_owner and not ctx.can("workspace.transfer"):
        raise ApiError(403, "FORBIDDEN", "Only an owner can change owners.")
    if current == "owner" and new != "owner" and service.owner_count(conn, ctx.workspace_id) <= 1:
        raise ApiError(409, "LAST_OWNER", "A workspace needs at least one owner. Transfer ownership first.")


@router.patch("/workspaces/{ws}/members/{user_id}", response_model=Ok)
def update_member(user_id: UUID, body: MemberPatch, ctx: MembersManage, request: Request, db: MetaDbDep) -> Ok:
    with db.transaction() as conn:
        current = _target_role(conn, ctx, user_id)
        _guard_owner_change(conn, ctx, current, body.role)
        conn.execute("UPDATE app.memberships SET role = %s WHERE workspace_id = %s AND user_id = %s",
                     (body.role, ctx.workspace_id, user_id))  # fmt: skip
        audit.record(conn, "member.role_change", request=request, workspace_id=ctx.workspace_id,
                     actor_user_id=ctx.user_id, target_type="user", target_id=user_id,
                     metadata={"from": current, "to": body.role})  # fmt: skip
    return Ok()


@router.delete("/workspaces/{ws}/members/{user_id}", response_model=Ok)
def remove_member(user_id: UUID, ws: UUID, session: SessionDep, request: Request, db: MetaDbDep) -> Ok:
    """Admins remove members; any member may remove themselves (leave)."""
    with db.pool.connection() as conn:
        me = service.membership(conn, ws, session.user_id)
    if me is None:
        raise ApiError(404, "NOT_FOUND", "Workspace not found.")
    ctx = WorkspaceContext(session=session, workspace=me, role=me["role"])
    if user_id != session.user_id and not ctx.can("members.manage"):
        raise ApiError(403, "FORBIDDEN", "Your role can't do this.", {"required": "admin", "role": ctx.role})
    with db.transaction() as conn:
        current = _target_role(conn, ctx, user_id)
        if user_id != session.user_id:
            _guard_owner_change(conn, ctx, current, None)
        elif current == "owner" and service.owner_count(conn, ws) <= 1:
            raise ApiError(409, "LAST_OWNER", "A workspace needs at least one owner. Transfer ownership first.")
        conn.execute("DELETE FROM app.memberships WHERE workspace_id = %s AND user_id = %s", (ws, user_id))
        audit.record(conn, "member.remove" if user_id != session.user_id else "member.leave",
                     request=request, workspace_id=ws, actor_user_id=session.user_id,
                     target_type="user", target_id=user_id, metadata={"role": current})  # fmt: skip
    return Ok()


@router.get("/workspaces/{ws}/audit", response_model=AuditPage)
def audit_log(
    ctx: AuditView,
    db: MetaDbDep,
    action: str | None = None,
    actor: UUID | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    cursor: str | None = None,
) -> AuditPage:
    try:
        before = int(cursor) if cursor else None
    except ValueError:
        raise ApiError(422, "validation_error", "Invalid cursor") from None
    with db.pool.connection() as conn:
        rows = conn.execute(
            """SELECT a.id, a.action, a.actor_user_id, u.email AS actor_email, a.target_type, a.target_id,
                      host(a.ip) AS ip, a.request_id, a.metadata, a.created_at
                 FROM app.audit_log a LEFT JOIN app.users u ON u.id = a.actor_user_id
                WHERE a.workspace_id = %(ws)s
                  AND (%(action)s::text IS NULL OR a.action = %(action)s OR a.action LIKE %(action)s || '.%%')
                  AND (%(actor)s::uuid IS NULL OR a.actor_user_id = %(actor)s)
                  AND (%(from)s::timestamptz IS NULL OR a.created_at >= %(from)s)
                  AND (%(to)s::timestamptz IS NULL OR a.created_at < %(to)s)
                  AND (%(before)s::bigint IS NULL OR a.id < %(before)s)
                ORDER BY a.id DESC LIMIT %(limit)s""",
            {"ws": ctx.workspace_id, "action": action, "actor": actor, "from": from_, "to": to,
             "before": before, "limit": limit + 1},
        ).fetchall()  # fmt: skip
    has_more = len(rows) > limit
    rows = rows[:limit]
    return AuditPage(items=[AuditEvent(**r) for r in rows],
                     next_cursor=str(rows[-1]["id"]) if has_more and rows else None)  # fmt: skip


__all__ = ["router", "rbac"]
