from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import get_db, utcnow
from ..deps import get_current_user
from ..models import LoginAttempt, Notification, User
from ..permissions import accessible_project_ids, permission_list
from ..security import create_token, hash_password, password_problems, verify_password
from ..serializers import iso, user_out

router = APIRouter(prefix="/api/auth", tags=["auth"])

# Constant-time-ish dummy hash so unknown emails cost the same as wrong passwords.
_DUMMY = hash_password("dummy-password-for-timing")


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)


class ChangePasswordIn(BaseModel):
    current_password: str
    new_password: str


def session_payload(db: Session, user: User) -> dict:
    ids = accessible_project_ids(db, user)
    unread = db.execute(select(func.count()).select_from(Notification).where(Notification.user_id == user.id,
                                                                           Notification.is_read.is_(False))).scalar_one()
    return {"user": user_out(user), "permissions": permission_list(user), "project_ids": None if ids is None else sorted(ids),
            "unread_notifications": unread}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    email = body.email.strip().lower()
    window = utcnow() - timedelta(minutes=s.login_lockout_minutes)
    failures = db.execute(select(func.count()).select_from(LoginAttempt).where(
        LoginAttempt.email == email, LoginAttempt.success.is_(False), LoginAttempt.created_at >= window)).scalar_one()
    if failures >= s.login_max_failures:
        raise HTTPException(429, f"Too many failed attempts. Try again in {s.login_lockout_minutes} minutes.")
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    ok = verify_password(body.password, user.password_hash if user else _DUMMY) and user is not None and user.is_active
    db.add(LoginAttempt(email=email, success=bool(ok), ip=request.client.host if request.client else None))
    if not ok:
        audit(db, None, "auth.login_failed", detail={"email": email}, actor_label=email, request=request)
        db.commit()
        raise HTTPException(401, "Invalid email or password.")
    user.last_login_at = utcnow()
    audit(db, user, "auth.login", entity_type="user", entity_id=user.id, request=request)
    db.commit()
    response.set_cookie(s.cookie_name, create_token(user.id, user.token_version), httponly=True, samesite="lax",
                        secure=s.cookie_secure, max_age=s.access_token_minutes * 60, path="/")
    return session_payload(db, user)


def _is_loopback(request: Request) -> bool:
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1", "localhost")


def _demo_allowed(request: Request) -> bool:
    return get_settings().demo_login_active and _is_loopback(request)


class DemoLoginIn(BaseModel):
    user_id: int


def _quick_access(user: User) -> bool:
    """Who the one-click panel may sign in: seeded SAMPLE accounts (unless hidden) and accounts at the configured team domains."""
    s = get_settings()
    domains = {d.strip().lower().lstrip("@") for d in s.demo_login_domains.split(",") if d.strip()}
    return (user.is_sample and s.demo_login_show_samples) or ((user.email or "").rsplit("@", 1)[-1].lower() in domains)


@router.get("/demo-accounts")
def demo_accounts(request: Request, db: Session = Depends(get_db)):
    """Seeded SAMPLE accounts for one-click login. Empty unless ENV=development and the caller is on loopback."""
    if not _demo_allowed(request):
        return {"enabled": False, "accounts": []}
    users = [u for u in db.execute(select(User).where(User.is_active.is_(True)).order_by(User.id)).scalars() if _quick_access(u)]
    return {"enabled": True, "accounts": [user_out(u) for u in users]}


@router.post("/demo-login")
def demo_login(body: DemoLoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if not _demo_allowed(request):
        raise HTTPException(404, "Not found.")
    user = db.get(User, body.user_id)
    if not user or not user.is_active or not _quick_access(user):
        raise HTTPException(404, "Demo account not found.")
    s = get_settings()
    user.last_login_at = utcnow()
    audit(db, user, "auth.demo_login", entity_type="user", entity_id=user.id, detail={"note": "one-click SAMPLE account login"}, request=request)
    db.commit()
    response.set_cookie(s.cookie_name, create_token(user.id, user.token_version), httponly=True, samesite="lax",
                        secure=s.cookie_secure, max_age=s.access_token_minutes * 60, path="/")
    return session_payload(db, user)


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    try:
        user = get_current_user(request, db)
        audit(db, user, "auth.logout", entity_type="user", entity_id=user.id, request=request)
        db.commit()
    except HTTPException:
        pass
    response.delete_cookie(s.cookie_name, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return session_payload(db, user)


@router.post("/change-password")
def change_password(body: ChangePasswordIn, request: Request, response: Response, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(400, "Current password is incorrect.")
    problems = password_problems(body.new_password)
    if problems:
        raise HTTPException(422, "Password must contain " + ", ".join(problems) + ".")
    if body.new_password == body.current_password:
        raise HTTPException(422, "Choose a different password.")
    user.password_hash = hash_password(body.new_password)
    user.must_change_password = False
    user.token_version += 1  # revoke other sessions
    audit(db, user, "auth.password_changed", entity_type="user", entity_id=user.id, request=request)
    db.commit()
    s = get_settings()
    response.set_cookie(s.cookie_name, create_token(user.id, user.token_version), httponly=True, samesite="lax",
                        secure=s.cookie_secure, max_age=s.access_token_minutes * 60, path="/")
    return session_payload(db, user)


@router.get("/notifications")
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()).limit(30)).scalars().all()
    return [{"id": n.id, "kind": n.kind, "message": n.message, "form_id": n.form_id, "is_read": n.is_read,
             "created_at": iso(n.created_at)} for n in rows]


@router.post("/notifications/read")
def mark_read(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    for n in db.execute(select(Notification).where(Notification.user_id == user.id, Notification.is_read.is_(False))).scalars():
        n.is_read = True
    db.commit()
    return {"ok": True}
