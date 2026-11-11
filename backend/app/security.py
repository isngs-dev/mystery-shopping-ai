import base64
import hashlib
import re
import secrets
from datetime import timedelta

import bcrypt
import jwt

from .config import get_settings
from .db import utcnow

ALGO = "HS256"


def _prehash(password: str) -> bytes:
    # bcrypt truncates/rejects >72 bytes; a SHA-256 prehash keeps long passphrases safe.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    rounds = 4 if get_settings().env == "test" else 12
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt(rounds=rounds)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), hashed.encode())
    except ValueError:
        return False


def password_problems(password: str) -> list[str]:
    s = get_settings()
    problems = []
    if len(password) < s.min_password_length:
        problems.append(f"at least {s.min_password_length} characters")
    if not re.search(r"[a-z]", password) or not re.search(r"[A-Z]", password):
        problems.append("both upper- and lower-case letters")
    if not re.search(r"\d", password):
        problems.append("a number")
    return problems


def generate_temp_password() -> str:
    return f"Tmp-{secrets.token_urlsafe(9)}-9a"


def create_token(user_id: int, token_version: int) -> str:
    s = get_settings()
    exp = utcnow() + timedelta(minutes=s.access_token_minutes)
    return jwt.encode({"sub": str(user_id), "tv": token_version, "exp": exp}, s.secret_key, algorithm=ALGO)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, get_settings().secret_key, algorithms=[ALGO])
    except jwt.PyJWTError:
        return None
