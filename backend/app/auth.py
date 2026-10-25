"""Accounts, sessions and project access control.

With AUTH_ENABLED=false (the default) every request acts as a built-in local
user who owns everything, so single-user setups need no login.
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request, WebSocket
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import (
    Asset, AuthSession, BaselineRun, DataNeed, DatasetVersion, Export, Pipeline, Project, ReviewItem, SearchResult,
    TeamMember, User,
)

COOKIE = "df_session"
LOCAL_EMAIL = "local@dataforge.local"
ROLES = ("viewer", "editor", "owner")
_SCRYPT = {"n": 2**14, "r": 8, "p": 1}


# ---- passwords --------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, dklen=32, **_SCRYPT)
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"scrypt${_SCRYPT['n']}${_SCRYPT['r']}${_SCRYPT['p']}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), dklen=len(expected),
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# ---- login throttling -------------------------------------------------------

class _Throttle:
    """At most `limit` failed logins per key in `window` seconds."""

    def __init__(self, limit: int = 5, window: float = 300):
        self.limit, self.window = limit, window
        self.failures: dict[str, list[float]] = {}
        self.lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.failures.get(key, []) if now - t < self.window]
            self.failures[key] = recent
            if len(recent) >= self.limit:
                raise HTTPException(429, "too many failed attempts; try again in a few minutes")

    def fail(self, key: str) -> None:
        with self.lock:
            self.failures.setdefault(key, []).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self.lock:
            self.failures.pop(key, None)


throttle = _Throttle()


# ---- sessions ---------------------------------------------------------------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: Session, user: User) -> str:
    token = secrets.token_urlsafe(32)
    days = get_settings().session_days
    db.add(AuthSession(user_id=user.id, token_hash=_token_hash(token),
                       expires_at=datetime.now(timezone.utc) + timedelta(days=days)))
    db.commit()
    return token


def revoke_session(db: Session, token: str) -> None:
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if session is not None:
        db.delete(session)
        db.commit()


def _user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    session = db.scalar(select(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
    if session is None:
        return None
    expires = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
    if expires < datetime.now(timezone.utc):
        db.delete(session)
        db.commit()
        return None
    return db.get(User, session.user_id)


def local_user(db: Session) -> User:
    user = db.scalar(select(User).where(User.email == LOCAL_EMAIL))
    if user is None:
        user = User(email=LOCAL_EMAIL, name="Local user")
        db.add(user)
        db.commit()
    return user


def _bearer(header: str | None) -> str | None:
    if header and header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    if not get_settings().auth_enabled:
        return local_user(db)
    user = _user_for_token(db, request.cookies.get(COOKIE) or _bearer(request.headers.get("authorization")))
    if user is None:
        raise HTTPException(401, "sign in required")
    return user


def websocket_user(websocket: WebSocket, db: Session) -> User | None:
    if not get_settings().auth_enabled:
        return local_user(db)
    return _user_for_token(db, websocket.cookies.get(COOKIE) or websocket.query_params.get("token"))


# ---- access control ---------------------------------------------------------

def project_role(db: Session, user: User, project: Project) -> str | None:
    if not get_settings().auth_enabled:
        return "owner"
    if project.owner_id == user.id:
        return "owner"
    if project.team_id is not None:
        member = db.scalar(select(TeamMember).where(TeamMember.team_id == project.team_id, TeamMember.user_id == user.id))
        if member is not None:
            return member.role
    return None


def accessible_project_ids(db: Session, user: User):
    """A subquery of project ids the user can see."""
    if not get_settings().auth_enabled:
        return select(Project.id)
    teams = select(TeamMember.team_id).where(TeamMember.user_id == user.id)
    return select(Project.id).where((Project.owner_id == user.id) | (Project.team_id.in_(teams)))


def require_project(db: Session, user: User, project_id: int, write: bool = False) -> Project:
    project = db.get(Project, project_id)
    role = project_role(db, user, project) if project is not None else None
    if role is None:
        # Same answer for "missing" and "not yours", so ids can't be probed.
        raise HTTPException(404, "project not found")
    if write and role == "viewer":
        raise HTTPException(403, "viewers can't change this project")
    return project


def _project_id_of(db: Session, kind: str, obj_id: int) -> int | None:
    if kind == "project":
        return obj_id
    if kind == "need":
        return db.scalar(select(DataNeed.project_id).where(DataNeed.id == obj_id))
    if kind == "result":
        return db.scalar(select(DataNeed.project_id).join(SearchResult).where(SearchResult.id == obj_id))
    if kind == "asset":
        return db.scalar(
            select(DataNeed.project_id).join(SearchResult, SearchResult.need_id == DataNeed.id)
            .join(Asset, Asset.result_id == SearchResult.id).where(Asset.id == obj_id)
        )
    if kind == "pipeline":
        return db.scalar(select(Pipeline.project_id).where(Pipeline.id == obj_id))
    if kind == "version":
        return db.scalar(select(DatasetVersion.project_id).where(DatasetVersion.id == obj_id))
    if kind == "export":
        return db.scalar(select(DatasetVersion.project_id).join(Export, Export.version_id == DatasetVersion.id)
                         .where(Export.id == obj_id))
    if kind == "review":
        return db.scalar(select(DatasetVersion.project_id).join(ReviewItem, ReviewItem.version_id == DatasetVersion.id)
                         .where(ReviewItem.id == obj_id))
    if kind == "baseline":
        return db.scalar(select(DatasetVersion.project_id).join(BaselineRun, BaselineRun.version_id == DatasetVersion.id)
                         .where(BaselineRun.id == obj_id))
    raise ValueError(kind)


class Access:
    """Route dependency: resolve the project behind `{kind}_id` and check the user's role on it."""

    def __init__(self, kind: str, write: bool = False):
        self.kind, self.write = kind, write

    def __call__(self, request: Request, db: Session = Depends(get_db), user: User = Depends(current_user)) -> Project:
        raw = request.path_params.get(f"{self.kind}_id")
        try:
            obj_id = int(raw)
        except (TypeError, ValueError):
            raise HTTPException(404, "not found") from None
        project_id = _project_id_of(db, self.kind, obj_id)
        if project_id is None:
            raise HTTPException(404, f"{self.kind} not found")
        return require_project(db, user, project_id, self.write)


def read(kind: str):
    return Depends(Access(kind))


def write(kind: str):
    return Depends(Access(kind, write=True))
