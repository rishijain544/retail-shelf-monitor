"""
backend/app/core/security.py

Password hashing and JWT access-token helpers shared by the auth endpoints.

* Passwords are hashed with bcrypt through passlib's CryptContext. Plaintext
  passwords are never persisted, returned, or logged.
* Access tokens are signed JWTs (HS256). `sub` is the user id (as a string, as
  RFC 7519 / PyJWT require) and `exp` comes from ACCESS_TOKEN_EXPIRE_MINUTES.
* Every token carries a `purpose` claim. Only `access` tokens unlock the API; the
  short-lived `mfa_challenge` and `mfa_setup` tokens issued during sign-in are refused
  by `get_current_user` and are accepted by exactly one endpoint each. Treating a
  missing claim as invalid means a token minted before purposes existed cannot be
  replayed as a full session.
* Each account's TOTP secret is encrypted at rest with Fernet using
  `settings.MFA_ENCRYPTION_KEY`.

The signing key is `settings.SECRET_KEY`. When that is unset — which is only
allowed outside production, see config.py — a random per-process key is used so
local development still works, at the cost of tokens not surviving a restart.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Union

import jwt
from cryptography.fernet import Fernet, InvalidToken
from passlib.context import CryptContext

from app.core.config import settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"

# Token purposes. Every token is minted for exactly one of these.
PURPOSE_ACCESS = "access"
PURPOSE_MFA_CHALLENGE = "mfa_challenge"
PURPOSE_MFA_SETUP = "mfa_setup"

# A password check alone proves nothing yet, so its follow-up token is short-lived:
# long enough to type a 6-digit code, short enough that a stolen challenge token is
# near-useless. The setup token is longer because enrolment involves opening an
# authenticator app and confirming a code.
MFA_CHALLENGE_EXPIRE_MINUTES = 5
MFA_SETUP_EXPIRE_MINUTES = 15

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


def create_token(
    subject: Union[str, int],
    *,
    purpose: str,
    expires_delta: timedelta,
) -> str:
    """Sign a JWT for `subject` that is only valid for `purpose`."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(subject),
        "purpose": purpose,
        "iat": now,
        "exp": now + expires_delta,
    }
    return jwt.encode(payload, _signing_key(), algorithm=ALGORITHM)


def create_access_token(
    subject: Union[str, int],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Sign a full-session access token identifying `subject` (the user id)."""
    if expires_delta is None:
        expires_delta = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return create_token(subject, purpose=PURPOSE_ACCESS, expires_delta=expires_delta)


def create_mfa_challenge_token(subject: Union[str, int]) -> str:
    """Sign the token returned after a correct password when MFA is enrolled.

    It authenticates *the password step only*: it opens POST /auth/mfa/verify and no
    other route.
    """
    return create_token(
        subject,
        purpose=PURPOSE_MFA_CHALLENGE,
        expires_delta=timedelta(minutes=MFA_CHALLENGE_EXPIRE_MINUTES),
    )


def create_mfa_setup_token(subject: Union[str, int]) -> str:
    """Sign the token that lets an account enrol an authenticator."""
    return create_token(
        subject,
        purpose=PURPOSE_MFA_SETUP,
        expires_delta=timedelta(minutes=MFA_SETUP_EXPIRE_MINUTES),
    )


def decode_token(token: str, *, purpose: str) -> Optional[dict[str, Any]]:
    """Validate `token` for `purpose`, or return None.

    Never raises — callers turn None into a 401. A token with no `purpose` claim is
    rejected even for `access`: tokens issued before purposes existed could otherwise
    be presented as full sessions forever.
    """
    try:
        payload = jwt.decode(token, _signing_key(), algorithms=[ALGORITHM])
    except jwt.PyJWTError as exc:
        logger.debug("Rejected token: %s", exc)
        return None

    if payload.get("purpose") != purpose:
        # No log of the token itself; the mismatch is enough to explain a 401.
        logger.debug(
            "Rejected token for purpose %r: token purpose was %r.",
            purpose,
            payload.get("purpose"),
        )
        return None
    return payload


def decode_access_token(token: str) -> Optional[dict[str, Any]]:
    """Validate a full-session `token` and return its claims, or None."""
    return decode_token(token, purpose=PURPOSE_ACCESS)


def code_pepper() -> bytes:
    """Key material for keyed digests of short one-time codes.

    Derived from the JWT signing key so a database dump alone cannot brute-force a
    six-digit verification code offline. Rotating SECRET_KEY invalidates codes in
    flight, which is the correct trade: they live for minutes.
    """
    return hashlib.sha256(f"one-time-code:{_signing_key()}".encode()).digest()


def _fernet() -> Fernet:
    """The Fernet used for TOTP secrets, honouring MFA_ENCRYPTION_KEY.

    Outside production/staging the key may be unset (config.py refuses to start in a
    strict environment without it). Deriving it from the signing key keeps local
    development working while staying stable across restarts whenever SECRET_KEY is
    set — a random per-process key would make every enrolled account unreadable after
    a restart, which is exactly the failure this fallback must not create.
    """
    configured = (settings.MFA_ENCRYPTION_KEY or "").strip()
    if configured:
        return Fernet(configured.encode())

    logger.warning(
        "MFA_ENCRYPTION_KEY is not set; encrypting TOTP secrets with a key derived from "
        "SECRET_KEY. Set MFA_ENCRYPTION_KEY before enrolling real users."
    )
    derived = base64.urlsafe_b64encode(
        hashlib.sha256(f"mfa-secret:{_signing_key()}".encode()).digest()
    )
    return Fernet(derived)


def encrypt_mfa_secret(secret: str) -> str:
    """Encrypt an authenticator secret for storage."""
    return _fernet().encrypt(secret.encode()).decode()


def decrypt_mfa_secret(encrypted: Optional[str]) -> Optional[str]:
    """Decrypt a stored authenticator secret, or None if it cannot be recovered.

    Returns None instead of raising so a rotated/absent key produces a clear
    "re-enrol" response and a log line naming the recovery script, not a 500.
    """
    if not encrypted:
        return None
    try:
        return _fernet().decrypt(encrypted.encode()).decode()
    except (InvalidToken, ValueError):
        logger.error(
            "A stored MFA secret could not be decrypted (MFA_ENCRYPTION_KEY changed?). "
            "Use scripts/reset_mfa.py <email> to clear it for that account."
        )
        return None
