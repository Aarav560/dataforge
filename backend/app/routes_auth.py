"""Sign-up/login, teams and project sharing."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import (
    COOKIE, LOCAL_EMAIL, ROLES, create_session, current_user, hash_password, project_role, require_project,
    revoke_session, throttle, verify_password,
)
from .config import get_settings
from .db import get_db
from .models import Project, Team, TeamMember, User

router = APIRouter(prefix="/api")

EMAIL = r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$"


class SignUp(BaseModel):
    email: str = Field(pattern=EMAIL, max_length=320)
    password: str = Field(min_length=10, max_length=200)
    name: str = Field(default="", max_length=200)


class Login(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=200)


def _user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "name": u.name}


def _set_cookie(response: Response, token: str) -> None:
    s = get_settings()
    response.set_cookie(COOKIE, token, max_age=s.session_days * 86400, httponly=True, samesite="lax",
                        secure=s.secure_cookies, path="/")


@router.post("/auth/signup", status_code=201)
def signup(body: SignUp, response: Response, db: Session = Depends(get_db)) -> dict:
    settings = get_settings()
    if not settings.auth_enabled:
        raise HTTPException(400, "accounts are disabled (AUTH_ENABLED=false)")
    email = body.email.strip().lower()
    real_users = db.scalar(select(func.count()).select_from(User).where(User.email != LOCAL_EMAIL)) or 0
    if not settings.open_signup and real_users > 0:
        raise HTTPException(403, "sign-up is closed; ask a team owner to invite you")
    if db.scalar(select(User).where(User.email == email)) is not None:
        raise HTTPException(409, "an account with this email already exists")
    user = User(email=email, name=body.name.strip(), password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    _set_cookie(response, create_session(db, user))
    return _user_out(user)


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    if not get_settings().auth_enabled:
        raise HTTPException(400, "accounts are disabled (AUTH_ENABLED=false)")
    email = body.email.strip().lower()
    key = f"{email}|{request.client.host if request.client else ''}"
    throttle.check(key)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.password_hash or not verify_password(body.password, user.password_hash):
        throttle.fail(key)
        raise HTTPException(401, "wrong email or password")
    throttle.reset(key)
    _set_cookie(response, create_session(db, user))
    return _user_out(user)


@router.post("/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    token = request.cookies.get(COOKIE)
    if token:
        revoke_session(db, token)
    response.delete_cookie(COOKIE, path="/")


@router.get("/auth/me")
def me(user: User = Depends(current_user)) -> dict:
    return {**_user_out(user), "auth_enabled": get_settings().auth_enabled}


@router.post("/auth/token", status_code=201)
def issue_token(db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """A bearer token for scripts and the Claude connector (valid like a login session)."""
    return {"token": create_session(db, user), "expires_in_days": get_settings().session_days}


# ---- teams ------------------------------------------------------------------

class TeamCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberIn(BaseModel):
    email: str = Field(max_length=320)
    role: str = Field(default="editor", pattern="^(viewer|editor|owner)$")


class RoleIn(BaseModel):
    role: str = Field(pattern="^(viewer|editor|owner)$")


def require_team_role(db: Session, user: User, team_id: int, minimum: str) -> Team:
    team = db.get(Team, team_id)
    member = db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user.id)) if team else None
    if member is None:
        raise HTTPException(404, "team not found")
    if ROLES.index(member.role) < ROLES.index(minimum):
        raise HTTPException(403, f"needs the {minimum} role in this team")
    return team


def _team_out(db: Session, team: Team, user: User) -> dict:
    members = db.scalars(select(TeamMember).where(TeamMember.team_id == team.id)).all()
    mine = next((m.role for m in members if m.user_id == user.id), None)
    return {
        "id": team.id,
        "name": team.name,
        "role": mine,
        "members": [{"user_id": m.user_id, "email": m.user.email, "name": m.user.name, "role": m.role} for m in members],
    }


@router.post("/teams", status_code=201)
def create_team(body: TeamCreate, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    team = Team(name=body.name.strip())
    team.members.append(TeamMember(user_id=user.id, role="owner"))
    db.add(team)
    db.commit()
    return _team_out(db, team, user)


@router.get("/teams")
def list_teams(db: Session = Depends(get_db), user: User = Depends(current_user)) -> list[dict]:
    teams = db.scalars(select(Team).join(TeamMember).where(TeamMember.user_id == user.id).order_by(Team.name)).all()
    return [_team_out(db, t, user) for t in teams]


@router.post("/teams/{team_id}/members", status_code=201)
def add_member(team_id: int, body: MemberIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    team = require_team_role(db, user, team_id, "owner")
    invitee = db.scalar(select(User).where(User.email == body.email.strip().lower()))
    if invitee is None:
        raise HTTPException(404, "no account with that email; ask them to sign up first")
    existing = db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == invitee.id))
    if existing is not None:
        existing.role = body.role
    else:
        db.add(TeamMember(team_id=team_id, user_id=invitee.id, role=body.role))
    db.commit()
    return _team_out(db, team, user)


def _owners(db: Session, team_id: int) -> int:
    return db.scalar(select(func.count()).select_from(TeamMember)
                     .where(TeamMember.team_id == team_id, TeamMember.role == "owner")) or 0


@router.patch("/teams/{team_id}/members/{user_id}")
def change_role(team_id: int, user_id: int, body: RoleIn, db: Session = Depends(get_db),
                user: User = Depends(current_user)) -> dict:
    team = require_team_role(db, user, team_id, "owner")
    member = db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id))
    if member is None:
        raise HTTPException(404, "not a member")
    if member.role == "owner" and body.role != "owner" and _owners(db, team_id) == 1:
        raise HTTPException(409, "a team needs at least one owner")
    member.role = body.role
    db.commit()
    return _team_out(db, team, user)


@router.delete("/teams/{team_id}/members/{user_id}", status_code=204)
def remove_member(team_id: int, user_id: int, db: Session = Depends(get_db), user: User = Depends(current_user)) -> None:
    # Owners remove anyone; everyone may leave.
    if user_id != user.id:
        require_team_role(db, user, team_id, "owner")
    else:
        require_team_role(db, user, team_id, "viewer")
    member = db.scalar(select(TeamMember).where(TeamMember.team_id == team_id, TeamMember.user_id == user_id))
    if member is None:
        raise HTTPException(404, "not a member")
    if member.role == "owner" and _owners(db, team_id) == 1:
        raise HTTPException(409, "a team needs at least one owner")
    db.delete(member)
    db.commit()


class ShareIn(BaseModel):
    team_id: int | None


@router.put("/projects/{project_id}/team")
def share_project(project_id: int, body: ShareIn, db: Session = Depends(get_db), user: User = Depends(current_user)) -> dict:
    """Move a project into a team (shared) or back to the owner alone (team_id null)."""
    project = require_project(db, user, project_id, write=True)
    if project_role(db, user, project) != "owner":
        raise HTTPException(403, "only an owner can change who a project is shared with")
    if body.team_id is not None:
        require_team_role(db, user, body.team_id, "editor")
    project.team_id = body.team_id
    db.commit()
    return {"id": project.id, "team_id": project.team_id}
