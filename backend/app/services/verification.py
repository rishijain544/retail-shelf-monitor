"""
backend/app/services/verification.py

Email verification for new accounts.

The rule this service enforces: **an address only becomes a working login once a code
delivered to that address comes back**. Signup therefore produces no access token at
all — only a pending row plus a mailed code — so a typo'd or someone else's address
cannot be turned into an account that receives another store's alerts.

Storage and comparison
----------------------
The six-digit code itself is never stored. What is stored is
``HMAC-SHA256(secret, f"{user_id}:{code}")``. A fast unkeyed hash would not be enough:
six digits is a million candidates, i.e. milliseconds to brute-force from a database
dump. The key makes an offline guess as hard as forging a JWT, and every comparison
uses ``hmac.compare_digest`` so a wrong code cannot be narrowed down by timing.

Abuse limits
------------
* At most ``settings.MAX_CODE_ATTEMPTS`` wrong guesses per code, after which the row is
  burned (``consumed_at`` set) and a new code has to be requested.
* A resend inside ``RESEND_COOLDOWN_SECONDS`` is refused, so the endpoint cannot be used
  to mail-bomb an address.
* A code expires after ``settings.VERIFICATION_CODE_TTL_MINUTES``.
Issuing a new code invalidates every earlier unconsumed one for that account, so at most
one code is ever valid.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.core.config import settings
from app.core.security import code_pepper
from app.models.email_verification_code import EmailVerificationCode
from app.models.user import User
from app.services import email_service

logger = logging.getLogger(__name__)

CODE_DIGITS = 6
# One code per 60 seconds per account: enough for a lost email, useless for spam.
RESEND_COOLDOWN_SECONDS = 60

# Outcomes of verify(); the endpoint maps them onto one generic 400 so the response
# cannot be used to tell "expired" from "never existed".
RESULT_OK = "ok"
RESULT_INVALID = "invalid"
RESULT_EXPIRED = "expired"
RESULT_BURNED = "burned"


def generate_code() -> str:
    """A cryptographically random zero-padded 6-digit code."""
    return f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"


def hash_code(code: str, user_id: int) -> str:
    """Keyed digest of `code` for `user_id`.

    Binding the user id into the digest means a code copied from one account's email
    cannot satisfy another account's row.
    """
    message = f"{user_id}:{code.strip()}".encode()
    return hmac.new(code_pepper(), message, hashlib.sha256).hexdigest()


def _active_code(db: Session, user: User) -> Optional[EmailVerificationCode]:
    """The newest unconsumed code for `user`, if any."""
    return (
        db.query(EmailVerificationCode)
        .filter(
            EmailVerificationCode.user_id == user.id,
            EmailVerificationCode.consumed_at.is_(None),
        )
        .order_by(EmailVerificationCode.id.desc())
        .first()
    )


def seconds_until_resend_allowed(db: Session, user: User) -> int:
    """Seconds the caller must still wait before a new code may be issued."""
    latest = (
        db.query(EmailVerificationCode)
        .filter(EmailVerificationCode.user_id == user.id)
        .order_by(EmailVerificationCode.id.desc())
        .first()
    )
    if latest is None:
        return 0
    created = as_utc(latest.created_at) or utcnow()
    elapsed = (utcnow() - created).total_seconds()
    remaining = RESEND_COOLDOWN_SECONDS - int(elapsed)
    return max(0, remaining)


def issue_and_send(db: Session, user: User, *, store_name: Optional[str] = None) -> bool:
    """Create a fresh code for `user` and email it.

    Returns True when the message was accepted by a transport. A False return is not an
    error for the caller: the account exists and unverified, so a later resend works
    once the relay is configured.
    """
    now = utcnow()

    # One valid code at a time: an older email must stop working the moment a new one
    # is requested, otherwise every resend widens the window for guessing.
    db.query(EmailVerificationCode).filter(
        EmailVerificationCode.user_id == user.id,
        EmailVerificationCode.consumed_at.is_(None),
    ).update({EmailVerificationCode.consumed_at: now}, synchronize_session=False)

    code = generate_code()
    db.add(
        EmailVerificationCode(
            user_id=user.id,
            code_hash=hash_code(code, user.id),
            expires_at=now + timedelta(minutes=settings.VERIFICATION_CODE_TTL_MINUTES),
            attempts=0,
            created_at=now,
        )
    )
    db.commit()

    delivered = email_service.send_verification_code(
        user.email, code, store_name=store_name
    )
    if delivered:
        logger.info(
            "auth: verification code mailed user_id=%s address=%s", user.id, user.email
        )
        return True

    # Development aid only, and only when there is no transport at all: an operator
    # with a configured relay gets no code in the logs, ever.
    if settings.DEV_LOG_VERIFICATION_CODES and email_service.transport() == "none":
        logger.warning(
            "DEV_LOG_VERIFICATION_CODES is on and no email transport is configured; "
            "the signup verification code for %s is %s",
            user.email,
            code,
        )
    else:
        logger.warning(
            "auth: could not deliver a verification code to user_id=%s "
            "(no email transport configured or delivery failed).", user.id
        )
    return False


def verify(db: Session, user: User, submitted_code: str) -> str:
    """Check `submitted_code` for `user` and consume it on success.

    Returns one of RESULT_OK / RESULT_INVALID / RESULT_EXPIRED / RESULT_BURNED. The
    caller turns everything but OK into the same generic 400 message.
    """
    row = _active_code(db, user)
    if row is None:
        return RESULT_INVALID

    now = utcnow()
    if as_utc(row.expires_at) <= now:
        row.consumed_at = now
        db.commit()
        return RESULT_EXPIRED

    if row.attempts >= settings.MAX_CODE_ATTEMPTS:
        row.consumed_at = now
        db.commit()
        return RESULT_BURNED

    expected = hash_code(submitted_code, user.id)
    if not hmac.compare_digest(expected, row.code_hash):
        row.attempts += 1
        expired_by_attempts = row.attempts >= settings.MAX_CODE_ATTEMPTS
        if expired_by_attempts:
            # Burn it: the code is unusable and a new one must be requested.
            row.consumed_at = now
        db.commit()
        # Never log the submitted or expected code; the counters are enough to explain
        # a burst of failures. The endpoint adds the client IP.
        logger.info(
            "auth: verify failed user_id=%s attempts=%s burned=%s",
            user.id,
            row.attempts,
            expired_by_attempts,
        )
        return RESULT_BURNED if expired_by_attempts else RESULT_INVALID

    row.consumed_at = now
    user.email_verified = True
    db.commit()
    logger.info("auth: verify ok user_id=%s", user.id)
    return RESULT_OK
