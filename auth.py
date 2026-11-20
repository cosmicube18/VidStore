"""
auth.py — Password hashing/verification + signed HttpOnly session cookie helpers.

Uses `bcrypt` directly.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Optional

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from fastapi import Cookie, HTTPException, status
from sqlalchemy.orm import Session

from database import User

# ---------------------------------------------------------------------------
# Load secret key
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent

# Check for local env files if not set
for env_name in ("Keys.env", ".env"):
    env_file = BASE_DIR / env_name
    if env_file.exists():
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    k, v = k.strip(), v.strip().strip('"').strip("'")
                    if k not in os.environ:
                        os.environ[k] = v

SECRET_KEY: str = os.environ.get("VIDSTORE_SECRET", "")
if not SECRET_KEY:
    # If in cloud/production and no secret set, warn or fallback safely
    SECRET_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or "vidstore-default-dev-secret-key-32b"

_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="vidstore-session")

COOKIE_NAME = "vs_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7  # 7 days in seconds


# ---------------------------------------------------------------------------
# Password hashing  (bcrypt directly)
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt, returns a utf-8 string."""
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(plain.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """Return True if plain matches the stored bcrypt hash."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Signed session cookie
# ---------------------------------------------------------------------------

def create_session_token(user_id: int) -> str:
    """Return a signed, URL-safe token encoding the user_id."""
    return _serializer.dumps({"uid": user_id})


def decode_session_token(token: str) -> Optional[int]:
    """
    Decode and verify the token.
    Returns the user_id or None if the token is invalid / expired.
    """
    try:
        data = _serializer.loads(token, max_age=SESSION_MAX_AGE)
        return int(data["uid"])
    except (BadSignature, SignatureExpired, KeyError, ValueError):
        return None


# ---------------------------------------------------------------------------
# FastAPI dependency — resolve the current user from the session cookie
# ---------------------------------------------------------------------------

def get_current_user(
    db: Session,
    vs_session: Optional[str] = Cookie(default=None),
) -> User:
    """
    Resolve the authenticated user from the signed session cookie.
    Raises HTTP 401 if the cookie is missing, invalid, or the user no longer exists.
    """
    if not vs_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    user_id = decode_session_token(vs_session)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or invalid",
        )
    user: Optional[User] = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )
    return user
