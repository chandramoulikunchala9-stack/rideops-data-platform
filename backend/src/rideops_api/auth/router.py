"""/auth: sign up, verify email, log in/out, password reset, current user (§13.1)."""

from fastapi import APIRouter, Request, Response, status

from rideops_api import audit
from rideops_api.auth import passwords, sessions
from rideops_api.auth.deps import LensSettingsDep, MetaDbDep, SessionDep
from rideops_api.auth.schemas import (
    Accepted,
    ForgotIn,
    LoginIn,
    MembershipOut,
    MeOut,
    Ok,
    ResetIn,
    SignupIn,
    TokenIn,
    UserOut,
)
from rideops_api.auth.tokens import digest
from rideops_api.errors import ApiError
from rideops_api.notifications.email import Email
from rideops_api.settings import LensSettings
from rideops_api.workspaces import service as workspaces

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_cookies(response: Response, raw: str, csrf: str, settings: LensSettings) -> None:
    max_age = settings.session_absolute_days * 86400
    response.set_cookie(sessions.COOKIE_NAME, raw, max_age=max_age, httponly=True,
                        secure=settings.session_cookie_secure, samesite="lax", path="/")  # fmt: skip
    # Readable by the SPA so it can echo it in X-CSRF-Token (double submit, FR-03).
    response.set_cookie(sessions.CSRF_COOKIE_NAME, csrf, max_age=max_age, httponly=False,
                        secure=settings.session_cookie_secure, samesite="lax", path="/")  # fmt: skip


def _clear_cookies(response: Response) -> None:
    for name in (sessions.COOKIE_NAME, sessions.CSRF_COOKIE_NAME):
        response.delete_cookie(name, path="/")


def _send(
    request: Request, to: str, subject: str, path: str, token: str, settings: LensSettings
) -> None:
    link = f"{settings.public_app_url.rstrip('/')}{path}?token={token}"
    request.app.state.email_sender.send(Email(to=to, subject=subject, body=f"{subject}:\n{link}\n"))


def _me(conn, session_user_id, csrf: str) -> MeOut:
    user = conn.execute(
        "SELECT id, email, display_name, email_verified FROM app.users WHERE id = %s",
        (session_user_id,),
    ).fetchone()
    rows = workspaces.list_for_user(conn, session_user_id)
    memberships = [MembershipOut(workspace_id=r["id"], name=r["name"], slug=r["slug"], role=r["role"])
                   for r in rows]  # fmt: skip
    return MeOut(
        user=UserOut(**user),
        memberships=memberships,
        current_workspace_id=memberships[0].workspace_id if memberships else None,
        csrf_token=csrf,
    )


@router.post("/signup", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def signup(body: SignupIn, request: Request, response: Response, db: MetaDbDep,
           settings: LensSettingsDep) -> MeOut:  # fmt: skip
    """Create the user and a personal workspace, email a verification link, and sign in."""
    problems = passwords.policy_problems(body.password, email=body.email)
    if problems:
        raise ApiError(422, "PASSWORD_WEAK", problems[0], {"problems": problems})
    password_hash = passwords.hash_password(body.password)  # outside the transaction (slow)
    with db.transaction() as conn:
        exists = conn.execute("SELECT 1 FROM app.users WHERE email = %s", (body.email,)).fetchone()
        if exists:
            raise ApiError(409, "EMAIL_TAKEN", "An account with this email already exists.")
        user = conn.execute(
            """INSERT INTO app.users (email, display_name, password_hash, last_login_at)
               VALUES (%s, %s, %s, now())
               ON CONFLICT (email) DO NOTHING RETURNING id""",
            (body.email, body.display_name.strip(), password_hash),
        ).fetchone()
        if user is None:  # a concurrent sign-up with the same email won the race
            raise ApiError(409, "EMAIL_TAKEN", "An account with this email already exists.")
        ws = workspaces.create(
            conn, user["id"], body.display_name.strip() + workspaces.PERSONAL_SUFFIX
        )
        token = sessions.issue_user_token(conn, user["id"], "verify_email")
        raw, csrf = sessions.create(conn, user["id"], request=request,
                                    absolute_days=settings.session_absolute_days)  # fmt: skip
        audit.record(conn, "user.signup", request=request, workspace_id=ws["id"],
                     actor_user_id=user["id"], target_type="user", target_id=user["id"])  # fmt: skip
        me = _me(conn, user["id"], csrf)
    _send(
        request, body.email, "Verify your RideOps Lens email", "/app/verify-email", token, settings
    )
    _set_cookies(response, raw, csrf, settings)
    return me


@router.post("/verify-email", response_model=Ok)
def verify_email(body: TokenIn, request: Request, db: MetaDbDep) -> Ok:
    with db.transaction() as conn:
        user_id = sessions.consume_user_token(conn, body.token, "verify_email")
        if user_id is None:
            raise ApiError(400, "TOKEN_INVALID", "This link is invalid or has expired.")
        conn.execute("UPDATE app.users SET email_verified = true WHERE id = %s", (user_id,))
        audit.record(conn, "user.verify_email", request=request, actor_user_id=user_id,
                     target_type="user", target_id=user_id)  # fmt: skip
    return Ok()


def _check_rate_limits(conn, email: str, ip: str | None, settings: LensSettings) -> None:
    failures = conn.execute(
        """SELECT count(*) AS n FROM app.login_attempts
            WHERE email = %s AND NOT succeeded
              AND created_at > now() - make_interval(mins => %s)""",
        (email, settings.login_failure_window_min),
    ).fetchone()["n"]
    if failures >= settings.login_max_failures:
        raise ApiError(429, "RATE_LIMITED", "Too many failed sign-in attempts. Try again later.",
                       {"retry_after_s": settings.login_failure_window_min * 60})  # fmt: skip
    if ip:
        per_ip = conn.execute(
            """SELECT count(*) AS n FROM app.login_attempts
                WHERE ip = %s AND created_at > now() - interval '1 minute'""",
            (ip,),
        ).fetchone()["n"]
        if per_ip >= settings.login_max_per_ip_per_min:
            raise ApiError(429, "RATE_LIMITED", "Too many requests; retry after 60 s.",
                           {"retry_after_s": 60})  # fmt: skip


@router.post("/login", response_model=MeOut)
def login(body: LoginIn, request: Request, response: Response, db: MetaDbDep,
          settings: LensSettingsDep) -> MeOut:  # fmt: skip
    ip = audit.client_ip(request)
    with db.pool.connection() as conn:
        _check_rate_limits(conn, body.email, ip, settings)
        user = conn.execute(
            "SELECT id, password_hash, is_disabled FROM app.users WHERE email = %s", (body.email,)
        ).fetchone()
    ok = passwords.verify_password(user["password_hash"] if user else None, body.password)
    ok = ok and user is not None and not user["is_disabled"]
    with db.transaction() as conn:
        conn.execute("INSERT INTO app.login_attempts (email, ip, succeeded) VALUES (%s, %s, %s)",
                     (body.email, ip, ok))  # fmt: skip
        if not ok:
            audit.record(conn, "user.login_failed", request=request,
                         actor_user_id=user["id"] if user else None)  # fmt: skip
    if not ok:  # generic message: never reveal whether the email exists (§18.2)
        raise ApiError(401, "LOGIN_FAILED", "Email or password is incorrect.")

    new_hash = (
        passwords.hash_password(body.password)
        if passwords.needs_rehash(user["password_hash"])
        else None
    )
    with db.transaction() as conn:
        # Session rotation: a fresh id on every login; an old cookie is simply replaced.
        old = request.cookies.get(sessions.COOKIE_NAME)
        if old:
            sessions.destroy(conn, digest(old))
        conn.execute("UPDATE app.users SET last_login_at = now(), password_hash = COALESCE(%s, password_hash) "
                     "WHERE id = %s", (new_hash, user["id"]))  # fmt: skip
        raw, csrf = sessions.create(conn, user["id"], request=request,
                                    absolute_days=settings.session_absolute_days)  # fmt: skip
        audit.record(conn, "user.login", request=request, actor_user_id=user["id"])
        me = _me(conn, user["id"], csrf)
    _set_cookies(response, raw, csrf, settings)
    return me


@router.post("/logout", response_model=Ok)
def logout(session: SessionDep, request: Request, response: Response, db: MetaDbDep) -> Ok:
    with db.transaction() as conn:
        sessions.destroy(conn, session.id_hash)
        audit.record(conn, "user.logout", request=request, actor_user_id=session.user_id)
    _clear_cookies(response)
    return Ok()


@router.post("/password/forgot", response_model=Accepted, status_code=status.HTTP_202_ACCEPTED)
def forgot_password(
    body: ForgotIn, request: Request, db: MetaDbDep, settings: LensSettingsDep
) -> Accepted:
    """Always 202, whether or not the email exists (no account enumeration)."""
    with db.transaction() as conn:
        user = conn.execute(
            "SELECT id FROM app.users WHERE email = %s AND NOT is_disabled", (body.email,)
        ).fetchone()
        token = sessions.issue_user_token(conn, user["id"], "password_reset") if user else None
        if user:
            audit.record(
                conn, "user.password_reset_requested", request=request, actor_user_id=user["id"]
            )
    if token:
        _send(
            request,
            body.email,
            "Reset your RideOps Lens password",
            "/app/reset-password",
            token,
            settings,
        )
    return Accepted()


@router.post("/password/reset", response_model=Ok)
def reset_password(body: ResetIn, request: Request, response: Response, db: MetaDbDep) -> Ok:
    with db.pool.connection() as conn:
        email_row = conn.execute(
            """SELECT u.email FROM app.user_tokens t JOIN app.users u ON u.id = t.user_id
                WHERE t.token_hash = %s""",
            (digest(body.token),),
        ).fetchone()
    problems = passwords.policy_problems(
        body.password, email=email_row["email"] if email_row else None
    )
    if problems:
        raise ApiError(422, "PASSWORD_WEAK", problems[0], {"problems": problems})
    password_hash = passwords.hash_password(body.password)
    with db.transaction() as conn:
        user_id = sessions.consume_user_token(conn, body.token, "password_reset")
        if user_id is None:
            raise ApiError(400, "TOKEN_INVALID", "This link is invalid or has expired.")
        # Owning the inbox proves the address, so a reset also verifies the email.
        conn.execute("UPDATE app.users SET password_hash = %s, email_verified = true WHERE id = %s",
                     (password_hash, user_id))  # fmt: skip
        sessions.destroy_all_for_user(conn, user_id)  # sign out everywhere
        audit.record(conn, "user.password_reset", request=request, actor_user_id=user_id,
                     target_type="user", target_id=user_id)  # fmt: skip
    _clear_cookies(response)
    return Ok()


@router.get("/me", response_model=MeOut)
def me(session: SessionDep, db: MetaDbDep) -> MeOut:
    with db.pool.connection() as conn:
        return _me(conn, session.user_id, session.csrf_token)
