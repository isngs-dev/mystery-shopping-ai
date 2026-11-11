from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .config import get_settings
from .db import get_db
from .models import User
from .permissions import require_perm
from .security import decode_token

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    s = get_settings()
    token = request.cookies.get(s.cookie_name)
    via_cookie = True
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token, via_cookie = auth[7:].strip(), False
    if not token:
        raise HTTPException(401, "Not authenticated.")
    # CSRF defence for cookie sessions: SameSite=Lax cookie + required custom header on unsafe methods.
    if via_cookie and request.method in UNSAFE and request.headers.get("x-requested-with") != "XMLHttpRequest":
        raise HTTPException(403, "Missing CSRF header.")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(401, "Session expired. Please sign in again.")
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active or user.token_version != payload.get("tv"):
        raise HTTPException(401, "Session is no longer valid.")
    return user


def needs(perm: str):
    """Dependency factory: authenticated + role holds `perm`."""

    def dep(user: User = Depends(get_current_user)) -> User:
        require_perm(user, perm)
        return user

    return dep
