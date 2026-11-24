import re

import pytest

from rideops_api.workspaces import rbac
from tests.lens.conftest import signup, unique_email


def _ws(me: dict) -> str:
    return me["current_workspace_id"]


def _invite_token(outbox, to: str) -> str:
    mail = next(m for m in reversed(outbox.outbox) if m.to == to)
    return re.search(r"token=([\w-]+)", mail.body).group(1)


def _join(owner, member_client, outbox, ws: str, role: str) -> dict:
    """Sign up a new user and add them to `ws` with `role` via a real invitation."""
    me = signup(member_client, unique_email(role))
    r = owner.post(
        f"/api/v1/workspaces/{ws}/invitations", json={"email": me["email"], "role": role}
    )
    assert r.status_code == 201, r.text
    r = member_client.post(f"/api/v1/invitations/{_invite_token(outbox, me['email'])}/accept")
    assert r.status_code == 200, r.text
    assert r.json()["role"] == role
    return me


def test_list_create_get_workspace(client):
    me = signup(client)
    r = client.post("/api/v1/workspaces", json={"name": "Data team"})
    assert r.status_code == 201
    created = r.json()
    assert created["role"] == "owner" and created["slug"].startswith("data-team-")
    names = [w["name"] for w in client.get("/api/v1/workspaces").json()]
    assert "Data team" in names and len(names) == 2
    got = client.get(f"/api/v1/workspaces/{_ws(me)}").json()
    assert got["settings"]["allow_insecure_connections"] is False


TENANT_ROUTES = [
    ("get", "/api/v1/workspaces/{ws}", None),
    ("patch", "/api/v1/workspaces/{ws}", {"name": "pwned"}),
    ("delete", "/api/v1/workspaces/{ws}", None),
    ("get", "/api/v1/workspaces/{ws}/members", None),
    ("post", "/api/v1/workspaces/{ws}/invitations", {"email": "x@example.com", "role": "viewer"}),
    ("patch", "/api/v1/workspaces/{ws}/members/{user}", {"role": "viewer"}),
    ("delete", "/api/v1/workspaces/{ws}/members/{user}", None),
    ("get", "/api/v1/workspaces/{ws}/audit", None),
]


def test_tenancy_isolation_on_every_route(client, new_client):
    """A user who is not a member gets 404 (not 403) on every workspace route: no existence leak."""
    victim = signup(client)
    attacker = new_client()
    signup(attacker)
    ws, user = _ws(victim), victim["user"]["id"]
    for method, path, body in TENANT_ROUTES:
        kwargs = {"json": body} if body is not None else {}
        r = getattr(attacker, method)(path.format(ws=ws, user=user), **kwargs)
        assert r.status_code == 404, (method, path, r.status_code, r.text)
        assert r.json()["error"]["code"] == "NOT_FOUND"
    assert client.get(f"/api/v1/workspaces/{ws}").json()["name"] != "pwned"
    assert ws not in [w["id"] for w in attacker.get("/api/v1/workspaces").json()]


def test_invitation_flow_and_rbac(client, new_client, outbox):
    owner = signup(client)
    ws = _ws(owner)
    viewer_c, editor_c, admin_c = new_client(), new_client(), new_client()
    viewer = _join(client, viewer_c, outbox, ws, "viewer")
    _join(client, editor_c, outbox, ws, "editor")
    _join(client, admin_c, outbox, ws, "admin")

    members = {
        m["email"]: m["role"] for m in viewer_c.get(f"/api/v1/workspaces/{ws}/members").json()
    }
    assert (
        members[owner["email"]] == "owner"
        and members[viewer["email"]] == "viewer"
        and len(members) == 4
    )

    # Viewers and editors cannot manage members or see the audit log.
    for c in (viewer_c, editor_c):
        r = c.post(
            f"/api/v1/workspaces/{ws}/invitations", json={"email": unique_email(), "role": "viewer"}
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "FORBIDDEN"
        assert c.get(f"/api/v1/workspaces/{ws}/audit").status_code == 403
        assert c.patch(f"/api/v1/workspaces/{ws}", json={"name": "x"}).status_code == 403
    # Admins can invite but not create owners, and cannot change network security settings.
    r = admin_c.post(
        f"/api/v1/workspaces/{ws}/invitations", json={"email": unique_email(), "role": "owner"}
    )
    assert r.status_code == 403
    r = admin_c.patch(
        f"/api/v1/workspaces/{ws}", json={"settings": {"allow_insecure_connections": True}}
    )
    assert r.status_code == 403
    r = admin_c.patch(
        f"/api/v1/workspaces/{ws}", json={"name": "Renamed", "settings": {"allowed_ports": [6543]}}
    )
    assert r.status_code == 200 and r.json()["settings"]["allowed_ports"] == [6543]
    assert admin_c.delete(f"/api/v1/workspaces/{ws}").status_code == 403
    # Admin promotes the viewer to editor, cannot demote the owner.
    assert (
        admin_c.patch(
            f"/api/v1/workspaces/{ws}/members/{viewer['user']['id']}", json={"role": "editor"}
        ).status_code
        == 200
    )
    r = admin_c.patch(
        f"/api/v1/workspaces/{ws}/members/{owner['user']['id']}", json={"role": "viewer"}
    )
    assert r.status_code == 403
    # The owner can enable insecure connections.
    r = client.patch(
        f"/api/v1/workspaces/{ws}", json={"settings": {"allow_insecure_connections": True}}
    )
    assert r.status_code == 200 and r.json()["settings"]["allow_insecure_connections"] is True


def test_invitation_must_match_email_and_is_single_use(client, new_client, outbox):
    owner = signup(client)
    ws = _ws(owner)
    invited = unique_email("invited")
    assert (
        client.post(
            f"/api/v1/workspaces/{ws}/invitations", json={"email": invited, "role": "editor"}
        ).status_code
        == 201
    )
    token = _invite_token(outbox, invited)
    stranger = new_client()
    signup(stranger)
    assert stranger.post(f"/api/v1/invitations/{token}/accept").status_code == 404
    right = new_client()
    signup(right, invited)
    assert right.post(f"/api/v1/invitations/{token}/accept").status_code == 200
    assert right.post(f"/api/v1/invitations/{token}/accept").status_code == 404
    r = client.post(
        f"/api/v1/workspaces/{ws}/invitations", json={"email": invited, "role": "viewer"}
    )
    assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_MEMBER"


def test_last_owner_protection_and_leave(client, new_client, outbox):
    owner = signup(client)
    ws = _ws(owner)
    uid = owner["user"]["id"]
    r = client.patch(f"/api/v1/workspaces/{ws}/members/{uid}", json={"role": "admin"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "LAST_OWNER"
    assert client.delete(f"/api/v1/workspaces/{ws}/members/{uid}").status_code == 409
    second_c = new_client()
    second = _join(client, second_c, outbox, ws, "owner")
    assert (
        client.patch(f"/api/v1/workspaces/{ws}/members/{uid}", json={"role": "admin"}).status_code
        == 200
    )
    # A member can leave on their own.
    viewer_c = new_client()
    viewer = _join(second_c, viewer_c, outbox, ws, "viewer")
    assert (
        viewer_c.delete(f"/api/v1/workspaces/{ws}/members/{viewer['user']['id']}").status_code
        == 200
    )
    assert viewer_c.get(f"/api/v1/workspaces/{ws}").status_code == 404
    assert second["email"]


def test_soft_delete_hides_workspace(client):
    me = signup(client)
    extra = client.post("/api/v1/workspaces", json={"name": "Temp"}).json()["id"]
    assert client.delete(f"/api/v1/workspaces/{extra}").status_code == 200
    assert client.get(f"/api/v1/workspaces/{extra}").status_code == 404
    assert extra not in [w["id"] for w in client.get("/api/v1/workspaces").json()]
    assert client.get(f"/api/v1/workspaces/{_ws(me)}").status_code == 200


def test_audit_log_filters_and_pagination(client, new_client, outbox):
    owner = signup(client)
    ws = _ws(owner)
    for _ in range(3):
        client.post(
            f"/api/v1/workspaces/{ws}/invitations", json={"email": unique_email(), "role": "viewer"}
        )
    client.patch(f"/api/v1/workspaces/{ws}", json={"name": "Audited"})
    page = client.get(f"/api/v1/workspaces/{ws}/audit", params={"limit": 2}).json()
    assert len(page["items"]) == 2 and page["next_cursor"]
    assert page["items"][0]["actor_email"] == owner["email"]
    assert page["items"][0]["request_id"]
    rest = client.get(
        f"/api/v1/workspaces/{ws}/audit", params={"limit": 50, "cursor": page["next_cursor"]}
    ).json()
    assert rest["next_cursor"] is None
    all_actions = [e["action"] for e in page["items"] + rest["items"]]
    assert all_actions.count("member.invite") == 3 and "workspace.update" in all_actions
    assert "user.signup" in all_actions
    invites = client.get(f"/api/v1/workspaces/{ws}/audit", params={"action": "member"}).json()[
        "items"
    ]
    assert {e["action"] for e in invites} == {"member.invite"}
    # Secrets never appear in audit metadata.
    for event in page["items"] + rest["items"]:
        assert "token" not in str(event["metadata"]) and "password" not in str(event["metadata"])


@pytest.mark.parametrize("role,action,allowed", [
    ("viewer", "connection.view", True), ("viewer", "connection.edit", False),
    ("editor", "query.run", True), ("editor", "connection.rotate_secret", False),
    ("admin", "audit.view", True), ("admin", "workspace.security", False),
    ("owner", "workspace.delete", True),
])  # fmt: skip
def test_permission_matrix(role, action, allowed):
    assert rbac.allows(role, action) is allowed
