"""
backend/app/services/mfa.py

Authenticator-app (TOTP) enrolment, verification and recovery codes.

Design points that matter when changing anything here:

* **The secret never leaves the database in a usable form.** It is Fernet-encrypted
  with ``settings.MFA_ENCRYPTION_KEY`` (a database dump is not a list of working
  authenticator seeds). The base32 secret is shown exactly once, in the
  ``/auth/mfa/setup/start`` response, so a user can type it into an app that cannot
  scan a QR code.
* **Codes cannot be replayed.** Accepted time steps are recorded on the user; a code
  whose step is not strictly greater than the last accepted one is rejected. Without
  that, an intercepted code stays valid for the rest of its 30-second window — long
  enough to be typed into a second session.
* **Comparison is constant-time.** ``pyotp`` compares with ``hmac.compare_digest``;
  recovery codes are matched with bcrypt over *every* unused row (no early exit) so
  response time does not reveal how many codes remain.
* **Recovery codes are the only way back in.** There is no password-reset flow, so
  ``scripts/reset_mfa.py`` exists for the lost-device-and-lost-codes case.
"""

from __future__ import annotations

import hmac
import logging
import re
import secrets
import time
from typing import Optional

import bcrypt
import pyotp
from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.core.config import settings
from app.core.security import decrypt_mfa_secret, encrypt_mfa_secret, verify_password
from app.models.mfa_recovery_code import MfaRecoveryCode
from app.models.user import User

logger = logging.getLogger(__name__)

TOTP_INTERVAL_SECONDS = 30
TOTP_DIGITS = 6
# Accept the previous, current and next step (±30s) for clock drift, matching
# pyotp's valid_window=1 semantics — but computed here so the accepted step can be
# recorded and replay-checked.
TOTP_DRIFT_STEPS = 1

RECOVERY_CODE_COUNT = 10
# 4 random bytes = 8 hex chars = 32 bits of entropy per code. bcrypt at the default
# cost (~250ms) would make one recovery-code login take 2.5s for ten rows, which is a
# denial-of-service lever rather than extra safety: the entropy plus the 5-attempt
# lockout is what protects these, not the cost factor.
_RECOVERY_CODE_ROUNDS = 10


def generate_secret() -> str:
    """A fresh base32 TOTP secret."""
    return pyotp.random_base32()


def provisioning_uri(user: User, secret: str) -> str:
    """otpauth:// URI for an authenticator app."""
    return pyotp.TOTP(secret).provisioning_uri(
        name=user.email, issuer_name=settings.MFA_ISSUER_NAME
    )


def store_secret(user: User, secret: str) -> None:
    """Encrypt and attach a *pending* secret (mfa_enabled stays false until confirm)."""
    user.mfa_secret_encrypted = encrypt_mfa_secret(secret)


def read_secret(user: User) -> Optional[str]:
    """The decrypted TOTP secret (pending during setup, active once enrolled).

    None means there is no secret, or MFA_ENCRYPTION_KEY changed and it can no longer
    be decrypted — either way a TOTP code cannot be checked, so a recovery code or
    scripts/reset_mfa.py is the way forward.
    """
    return decrypt_mfa_secret(user.mfa_secret_encrypted)


def current_step(at: Optional[float] = None) -> int:
    """The TOTP time step for `at` (seconds since the epoch), defaulting to now."""
    return int((time.time() if at is None else at) // TOTP_INTERVAL_SECONDS)


def verify_totp(secret: str, code: str, last_used_step: Optional[int]) -> Optional[int]:
    """Return the accepted time step for `code`, or None.

    The check is done by generating the expected code for each candidate step and
    comparing with ``hmac.compare_digest`` rather than calling ``TOTP.verify``: the
    caller needs to know *which* step matched in order to record it for replay
    protection, which ``verify`` does not report.
    """
    candidate = (code or "").strip().replace(" ", "")
    if len(candidate) != TOTP_DIGITS or not candidate.isdigit():
        return None

    totp = pyotp.TOTP(secret)
    now_step = current_step()
    floor = last_used_step or 0

    for step in range(now_step - TOTP_DRIFT_STEPS, now_step + TOTP_DRIFT_STEPS + 1):
        if step <= floor:
            # A code from an already-used step is a replay, not a slow clock.
            continue
        if hmac.compare_digest(totp.at(step * TOTP_INTERVAL_SECONDS), candidate):
            return step
    return None


def _normalize_recovery_code(raw: str) -> str:
    """Canonical form of a recovery code: lower-case hex, separators removed."""
    return re.sub(r"[\s-]", "", (raw or "")).lower()


def _hash_recovery_code(raw: str) -> str:
    """bcrypt digest of a recovery code, at a cost tuned for a 10-row scan.

    Uses the bcrypt library directly rather than the shared password context so the
    lower cost factor applies only to these short, single-use codes and never to a
    user's password.
    """
    digest = bcrypt.hashpw(
        _normalize_recovery_code(raw).encode(), bcrypt.gensalt(_RECOVERY_CODE_ROUNDS)
    )
    return digest.decode()


def generate_recovery_codes(db: Session, user: User) -> list[str]:
    """Replace every recovery code for `user`; return the new plaintext codes once.

    The plaintext exists only in the caller's response. Only bcrypt digests are
    persisted, and the plaintext is never logged.
    """
    db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user.id).delete(
        synchronize_session=False
    )

    codes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(4)
        codes.append(f"{raw[:4]}-{raw[4:]}")
        db.add(
            MfaRecoveryCode(
                user_id=user.id,
                code_hash=_hash_recovery_code(raw),
            )
        )
    db.flush()
    return codes


def consume_recovery_code(db: Session, user: User, submitted: str) -> bool:
    """Mark `submitted` used and return True when it matches an unused code.

    Every unused row is checked (no early break) so the response time does not reveal
    whether the guess was close, and the row is only marked after the loop.
    """
    normalized = _normalize_recovery_code(submitted)
    if not normalized:
        return False

    rows = (
        db.query(MfaRecoveryCode)
        .filter(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.used_at.is_(None),
        )
        .all()
    )

    matched: Optional[MfaRecoveryCode] = None
    for row in rows:
        if verify_password(normalized, row.code_hash) and matched is None:
            matched = row

    if matched is None:
        return False

    matched.used_at = utcnow()
    db.commit()
    logger.info("auth: recovery code used user_id=%s", user.id)
    return True


def count_unused_recovery_codes(db: Session, user: User) -> int:
    """How many recovery codes remain (used by the admin/logging views only)."""
    return (
        db.query(MfaRecoveryCode)
        .filter(
            MfaRecoveryCode.user_id == user.id,
            MfaRecoveryCode.used_at.is_(None),
        )
        .count()
    )


def clear_mfa(db: Session, user: User) -> None:
    """Remove enrolment and every recovery code for `user` (admin recovery path).

    Deliberately not reachable over HTTP: with no password-reset flow, an endpoint
    that clears a second factor from a password alone would undo MFA entirely.
    """
    db.query(MfaRecoveryCode).filter(MfaRecoveryCode.user_id == user.id).delete(
        synchronize_session=False
    )
    user.mfa_enabled = False
    user.mfa_secret_encrypted = None
    user.mfa_last_used_step = None
    user.failed_mfa_attempts = 0
    user.mfa_locked_until = None
    db.commit()


def lockout_remaining_seconds(user: User) -> int:
    """Seconds until an MFA lockout expires (0 when not locked)."""
    until = as_utc(user.mfa_locked_until)
    if until is None:
        return 0
    remaining = int((until - utcnow()).total_seconds())
    return max(0, remaining)
