"""Login: the /api/auth endpoints, and the checks that enforce each account's access.

The server decides every access. Hiding a link on a page protects nothing, because anyone can
type an address. docs/protocol.md has the table of who can do what.
"""

import asyncio
import threading
import time
from contextlib import closing
from pathlib import PurePath

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from rhu import accounts, db
from rhu.accounts import Session

router = APIRouter(prefix="/api/auth")

COOKIE = "healthpulz_session"
MAX_FAILED_LOGINS = 5
LOCKOUT_SECONDS = 60
MAX_PASSWORD_LENGTH = 200  # hashing is deliberately slow; don't do it for huge inputs

# WebSocket close codes; the pages send the user back to the login page on 4401.
CLOSE_NOT_LOGGED_IN = 4401
CLOSE_NOT_ALLOWED = 4403

# Which account each page folder belongs to. Everything else under web/ is public (styles, scripts).
PAGE_ROLES = {"node": accounts.BHU, "dashboard": accounts.RHU, "monitor": accounts.RHU}


class LoginRequest(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=MAX_PASSWORD_LENGTH)


class LoginThrottle:
    """Slows down password guessing. Counted per account and device, so one classmate's typos
    don't lock out everyone else sharing that BHU's account."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._failures: dict[tuple[str, str], tuple[int, float]] = {}  # (count, last failure time)

    def seconds_left(self, key: tuple[str, str]) -> int:
        with self._lock:
            count, last = self._failures.get(key, (0, 0.0))
            remaining = last + LOCKOUT_SECONDS - time.monotonic()
            if remaining <= 0:
                self._failures.pop(key, None)
                return 0
            return int(remaining) + 1 if count >= MAX_FAILED_LOGINS else 0

    def failed(self, key: tuple[str, str]) -> None:
        with self._lock:
            count, _ = self._failures.get(key, (0, 0.0))
            self._failures[key] = (count + 1, time.monotonic())

    def succeeded(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._failures.pop(key, None)


# ---- who is asking ---------------------------------------------------------------------

def optional_session(connection: HTTPConnection) -> Session | None:
    token = connection.cookies.get(COOKIE)
    if not token:
        return None
    with closing(db.connect(connection.app.state.db_path)) as conn:
        return accounts.session_for_token(conn, token)


def current_session(session: Session | None = Depends(optional_session)) -> Session:
    if session is None:
        raise HTTPException(status_code=401, detail="Please log in.")
    return session


def require_rhu(session: Session = Depends(current_session)) -> Session:
    if not session.is_rhu:
        raise HTTPException(status_code=403, detail="Only the RHU can do this.")
    return session


def require_own_records(barangay_id: str, session: Session = Depends(current_session)) -> Session:
    if not session.may_open_records(barangay_id):
        raise HTTPException(status_code=403, detail="These records belong to another health unit.")
    return session


def require_may_send_for(session: Session, barangay_id: str) -> None:
    if not session.may_send_for(barangay_id):
        raise HTTPException(status_code=403, detail="This account cannot send reports for that barangay.")


# ---- endpoints -------------------------------------------------------------------------

def _describe(session: Session) -> dict:
    return {
        "username": session.username, "role": session.role, "barangay_id": session.barangay_id, "home": session.home,
    }


@router.post("/login")
def log_in(login: LoginRequest, request: Request, response: Response) -> dict:
    throttle: LoginThrottle = request.app.state.login_throttle
    key = (login.username, request.client.host if request.client else "")
    wait = throttle.seconds_left(key)
    if wait:
        raise HTTPException(
            status_code=429,
            detail=f"Too many wrong passwords. Try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )

    with closing(db.connect(request.app.state.db_path)) as conn:
        result = accounts.log_in(conn, login.username, login.password)
    if result is None:
        throttle.failed(key)
        raise HTTPException(status_code=401, detail="Wrong health unit or password.")

    throttle.succeeded(key)
    token, session = result
    response.set_cookie(
        COOKIE,
        token,
        max_age=accounts.SESSION_HOURS * 3600,
        httponly=True,  # page scripts can't read it
        samesite="lax",  # other websites can't send requests with it
        secure=request.url.scheme == "https",
    )
    return _describe(session)


@router.post("/logout")
def log_out(request: Request, response: Response) -> dict:
    token = request.cookies.get(COOKIE)
    if token:
        with closing(db.connect(request.app.state.db_path)) as conn:
            accounts.log_out(conn, token)
    response.delete_cookie(COOKIE)
    return {"status": "ok"}


@router.get("/me")
def who_am_i(session: Session = Depends(current_session)) -> dict:
    return _describe(session)


# ---- pages -----------------------------------------------------------------------------

class GuardedPages(StaticFiles):
    """Serves web/, and sends people to the login page or to their own side."""

    async def get_response(self, path: str, scope) -> Response:
        parts = PurePath(path).parts
        folder = parts[0].lower() if parts else ""  # lower(): Windows file names ignore case
        role = PAGE_ROLES.get(folder)
        is_login_page = parts in ((), ("index.html",))
        if role is None and not is_login_page:
            return await super().get_response(path, scope)

        session = await asyncio.to_thread(optional_session, Request(scope))
        if is_login_page and session and session.home != "/":
            return RedirectResponse(session.home, status_code=303)
        if role and (session is None or session.role != role):
            return RedirectResponse(session.home if session else "/", status_code=303)

        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-store"  # so Back can't reopen a page after logging out
        return response
