"""Request dependencies: the signed-in session and CSRF enforcement (FR-02, FR-03)."""

from typing import Annotated

from fastapi import Depends, Request

from rideops_api.auth import sessions
from rideops_api.auth.tokens import same
from rideops_api.db import Database, get_meta_db
from rideops_api.errors import ApiError
from rideops_api.settings import LensSettings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MetaDbDep = Annotated[Database, Depends(get_meta_db)]


def get_lens_settings_dep(request: Request) -> LensSettings:
    return request.app.state.lens_settings


LensSettingsDep = Annotated[LensSettings, Depends(get_lens_settings_dep)]


def check_csrf(request: Request, session: sessions.Session) -> None:
    if request.method in SAFE_METHODS:
        return
    header = request.headers.get("x-csrf-token", "")
    if not header or not same(header, session.csrf_token):
        raise ApiError(403, "CSRF_FAILED", "Refresh the page and retry.")


def get_session(request: Request, db: MetaDbDep, settings: LensSettingsDep) -> sessions.Session:
    raw = request.cookies.get(sessions.COOKIE_NAME)
    session = None
    if raw:
        with db.pool.connection() as conn:
            session = sessions.lookup(conn, raw, idle_hours=settings.session_idle_hours)
    if session is None:
        raise ApiError(401, "UNAUTHENTICATED", "Sign in to continue.")
    check_csrf(request, session)
    request.state.user_id = session.user_id
    return session


SessionDep = Annotated[sessions.Session, Depends(get_session)]
