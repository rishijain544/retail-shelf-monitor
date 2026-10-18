"""
backend/app/core/security.py

Password hashing and JWT access-token helpers shared by the auth endpoints.

* Passwords are hashed with bcrypt through passlib's CryptContext. Plaintext
  passwords are never persisted, returned, or logged.
* Access tokens are signed JWTs (HS256). `sub` is the user id (as a string, as
  RFC 7519 / PyJWT require) and `exp` comes from ACCESS_TOKEN_EXPIRE_MINUTES.

The signing key is `settings.SECRET_KEY`. When that is unset — which is only
allowed outside production, see config.py — a random per-process key is used so
local development still works, at the cost of tokens not surviving a restart.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import jwt
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# bcrypt only considers the first 72 bytes of a password. Older bcrypt builds
# truncated silently, newer ones raise, so we truncate consistently on both the
# hash and the verify side to keep behaviour stable across upgrades.
_BCRYPT_MAX_BYTES = 72

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Random per-process fallback, generated on first use. Never a hardcoded value.
_ephemeral_secret: Optional[str] = None


def _signing_key() -> str:
    """Return the JWT signing key, or a random dev-only one if unconfigured."""
    global _ephemeral_secret

    if settings.SECRET_KEY:
        return settings.SECRET_KEY

    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_urlsafe(48)
        logger.warning(
            "SECRET_KEY is not set; signing JWTs with a random per-process key. "
            "Existing tokens will stop working when this process restarts. "
            "Set SECRET_KEY in backend/.env for stable sessions."
        )
    return _ephemeral_secret


def _prepare(password: str) -> str:
    """Trim `password` to bcrypt's 72-byte input limit without splitting a char."""
    encoded = password.encode("utf-8")
    if len(encoded) <= _BCRYPT_MAX_BYTES:
        return password
    return encoded[:_BCRYPT_MAX_BYTES].decode("utf-8", errors="ignore")


def hash_password(password: str) -> str:
    """Hash `password` for storage. The plaintext is never returned."""
    return pwd_context.hash(_prepare(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """True when `plain_password` matches the stored bcrypt `hashed_password`."""
    if not hashed_password:
        return False
    try:
        return pwd_context.verify(_prepare(plain_password), hashed_password)
    except ValueError:
        # Malformed / non-bcrypt hash in the database — treat as a failed login
        # rather than a 500.
        logger.warning("Stored password hash could not be parsed; rejecting login.")
        return False


def create_access_token(
    subject: Union[str, int],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Sign an access token identifying `subject` (the user id)."""
    now = datetime.now(timezone.utc)
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)

    payload = {
        "sub": str(subject),
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """
    Validate `token` and return its claims, or None when it is invalid, expired,
    or signed with a different key. Never raises — callers turn None into a 401.
    """
    try:
        return jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        logger.debug("Rejected access token: %s", exc)
        return None
