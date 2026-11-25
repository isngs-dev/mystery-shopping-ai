"""Forgot password: a one-time code is e-mailed to the account's address; the person enters it with a new password (extension or website).

Safeguards: the answer is the same for known and unknown e-mails (no account enumeration); at most 3 codes per account per hour; a code lives
`RESET_CODE_MINUTES`, works once, and after 5 wrong tries it is dead; only a keyed hash of the code is stored; a successful reset signs out every
session of the account and clears its failed-login lock-out. When no SMTP server is configured nothing is e-mailed: the Super Admins are notified
in the app (they can reset the password from Admin), and the person is told so.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from ..audit import audit
from ..config import get_settings
from ..db import utcnow
from ..enums import Role
from ..models import LoginAttempt, Notification, PasswordReset, User
from ..security import hash_password, password_problems
from . import mailer

ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"        # no 0/O, 1/I/L
MAX_PER_HOUR = 3
MAX_TRIES = 5
BAD_CODE = "That code is not valid or has expired. Ask for a new one."


class ResetError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def _hash(user_id: int, code: str) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"{user_id}:{_clean(code)}".encode(), hashlib.sha256).hexdigest()


def _clean(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def request_code(db: Session, email: str, ip: str | None = None) -> dict:
    """Same answer whatever the e-mail is. Returns {"emailed": bool} only for the caller's tests / logs, never the code."""
    email = (email or "").strip().lower()
    s = get_settings()
    msg_ok = "If that e-mail belongs to an account, a reset code has been sent to it. It is valid for %d minutes." % s.reset_code_minutes
    msg_no_mail = ("E-mail sending is not set up on this server yet, so no code could be sent. If the e-mail belongs to an account, "
                   "the Super Admins have been notified and can reset the password for you.")
    reply = {"ok": True, "message": msg_ok if mailer.configured() else msg_no_mail}
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if user is None or not user.is_active:
        return reply
    recent = db.execute(select(func.count()).select_from(PasswordReset).where(
        PasswordReset.user_id == user.id, PasswordReset.created_at >= utcnow() - timedelta(hours=1))).scalar_one()
    if recent >= MAX_PER_HOUR:
        return reply
    if not mailer.configured():
        for admin in db.execute(select(User).where(User.role == Role.SUPER_ADMIN.value, User.is_active.is_(True))).scalars():
            db.add(Notification(user_id=admin.id, kind="password_reset",
                                message=f"{user.name} ({user.email}) asked to reset their password and e-mail is not set up. Reset it in Admin > Users."[:500]))
        db.add(PasswordReset(user_id=user.id, code_hash="-", expires_at=utcnow(), used=True))      # counts toward the hourly limit
        audit(db, None, "auth.password_reset_requested", entity_type="user", entity_id=user.id, detail={"emailed": False}, actor_label=email)
        db.commit()
        return reply
    code = "".join(secrets.choice(ALPHABET) for _ in range(8))
    db.execute(update(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used.is_(False)).values(used=True))
    db.add(PasswordReset(user_id=user.id, code_hash=_hash(user.id, code), expires_at=utcnow() + timedelta(minutes=s.reset_code_minutes)))
    sent = mailer.send(user.email, "Your Mystery Shopping AI password reset code",
                       f"Hello {user.name},\n\nYour password reset code is:  {code[:4]}-{code[4:]}\n\nEnter it in the extension (or the website) together with your new password. "
                       f"It works once and expires in {s.reset_code_minutes} minutes.\n\nIf you did not ask for this, ignore this e-mail: your password has not changed.\n")
    audit(db, None, "auth.password_reset_requested", entity_type="user", entity_id=user.id, detail={"emailed": sent}, actor_label=email)
    db.commit()
    if not sent:
        reply["message"] = "The reset e-mail could not be sent right now. Try again in a few minutes, or ask a Super Admin to reset your password."
    return reply


def confirm(db: Session, email: str, code: str, new_password: str) -> dict:
    email = (email or "").strip().lower()
    user = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    row = None
    if user is not None and user.is_active:
        row = db.execute(select(PasswordReset).where(PasswordReset.user_id == user.id, PasswordReset.used.is_(False),
                                                     PasswordReset.expires_at > utcnow()).order_by(PasswordReset.id.desc())).scalars().first()
    if row is None or row.attempts >= MAX_TRIES:
        raise ResetError(BAD_CODE)
    if not hmac.compare_digest(row.code_hash, _hash(user.id, code)):
        row.attempts += 1
        db.commit()
        raise ResetError(BAD_CODE)
    problems = password_problems(new_password)
    if problems:
        raise ResetError("The new password needs " + ", ".join(problems) + ".", 422)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version += 1
    row.used = True
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == email))
    audit(db, None, "auth.password_reset_done", entity_type="user", entity_id=user.id, actor_label=email)
    db.commit()
    return {"ok": True, "message": "Your password was changed. Sign in with the new password."}
