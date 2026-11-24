"""Request/response models for /auth and /workspaces."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

Role = Literal["owner", "admin", "editor", "viewer"]


class SignupIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)
    display_name: str = Field(min_length=1, max_length=80)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=1024)


class TokenIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)


class ForgotIn(BaseModel):
    email: EmailStr


class ResetIn(BaseModel):
    token: str = Field(min_length=10, max_length=200)
    password: str = Field(min_length=1, max_length=1024)


class UserOut(BaseModel):
    id: UUID
    email: str
    display_name: str
    email_verified: bool


class MembershipOut(BaseModel):
    workspace_id: UUID
    name: str
    slug: str
    role: Role


class MeOut(BaseModel):
    user: UserOut
    memberships: list[MembershipOut]
    current_workspace_id: UUID | None
    csrf_token: str


class Accepted(BaseModel):
    status: Literal["accepted"] = "accepted"


class Ok(BaseModel):
    status: Literal["ok"] = "ok"


class WorkspaceIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)


class WorkspaceSettings(BaseModel):
    allow_insecure_connections: bool = False
    allowed_ports: list[int] = Field(default_factory=list, max_length=32)
    egress_allowlist: list[str] = Field(default_factory=list, max_length=64)


class WorkspacePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    settings: WorkspaceSettings | None = None


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    slug: str
    plan: str
    settings: WorkspaceSettings
    role: Role
    created_at: datetime


class MemberOut(BaseModel):
    user_id: UUID
    email: str
    display_name: str
    role: Role
    joined_at: datetime


class InvitationIn(BaseModel):
    email: EmailStr
    role: Role = "viewer"


class InvitationOut(BaseModel):
    id: UUID
    email: str
    role: Role
    expires_at: datetime


class MemberPatch(BaseModel):
    role: Role


class AuditEvent(BaseModel):
    id: int
    action: str
    actor_user_id: UUID | None
    actor_email: str | None
    target_type: str | None
    target_id: str | None
    ip: str | None
    request_id: str | None
    metadata: dict
    created_at: datetime


class AuditPage(BaseModel):
    items: list[AuditEvent]
    next_cursor: str | None
