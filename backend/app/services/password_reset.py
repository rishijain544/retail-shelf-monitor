"""
backend/app/services/password_reset.py

Forgot-password requests and applying the emailed link.

Two properties drive the shape of this module:

**It must not be an account oracle.** ``POST /auth/forgot-password`` answers the same
202 body for every address, and the database lookup plus the send happen in a background
task *after* the response is sent, so a known address does not take measurably longer
than an unknown one. (Login does the same thing with a dummy bcrypt hash.)

**A reset is a mailbox proof, not a login.** The token is 256 bits of
``secrets.token_urlsafe(32)``; only its SHA-256 digest is stored, and a successful reset
issues no session. The account must still sign in normally, MFA included.

Abuse limits, in layers:

* one email per ``PASSWORD_RESET_COOLDOWN_SECONDS`` per account (a second request inside
  the cooldown is ignored, with its own log line);
* a per-IP budget for ``/forgot-password`` and another for ``/reset-password``.

The per-IP budgets are process-local sliding windows. That is honest about its limit: a
multi-worker deployment gives each worker its own budget, and a restart clears it. They
exist to stop a single client from hammering the endpoint or spraying bad tokens, which
the per-account cooldown alone cannot do; a durable per-IP store would need its own table
and is deliberately out of scope for this flow.

Nothing here logs a token, a digest or a password. Auth events carry the user id and IP
only, and the dev-flag path that logs a link is restricted to "no email transport
configured at all", exactly like verification codes.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Deque, Optional, Tuple
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.core.clock import as_utc, utcnow
from app.core.config import settings
from app.core.database import SessionLocal
from app.core.security import hash_password
from app.models.password_reset_token import PasswordResetToken
from app.models.user import User
from app.services import email_service

logger = logging.getLogger(__name__)

# 32 bytes -> 256 bits of entropy in ~43 URL-safe characters.
TOKEN_BYTES = 32

RESULT_OK = "ok"
RESULT_INVALID = "invalid"

# Per-IP sliding windows (process-local; see the module docstring).
IP_WINDOW_SECONDS = 3600
FORGOT_MAX_PER_IP = 10
RESET_MAX_PER_IP = 20

_hits: dict[tuple[str, str], Deque[float]] = defaultdict(deque)
_hits_lock = threading.Lock()


def allow(bucket: str, ip: str, *, limit: int, now: Optional[datetime] = None) -> bool:
    """Record a hit for `ip` in `bucket` and report whether it fits the budget.

    A sliding window over the last ``IP_WINDOW_SECONDS``: denied requests are not
    recorded, so a client that keeps retrying cannot extend its own ban.
    """
    moment = (now or utcnow()).timestamp()
    cutoff = moment - IP_WINDOW_SECONDS

    with _hits_lock:
        hits = _hits[(bucket, ip)]
        while hits and hits[0] <= cutoff:
            hits.popleft()
        if len(hits) >= limit:
            return False
        hits.append(moment)
        return True


def reset_rate_limits() -> None:
    """Forget every recorded hit (used by tests; harmless in production)."""
    with _hits_lock:
        _hits.clear()


def hash_token(token: str) -> str:
    """SHA-256 hex digest of `token` — the only form ever stored."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def reset_link(token: str) -> str:
    """The link mailed to the user, built from configured settings only."""
    base = (settings.FRONTEND_BASE_URL or "").strip().rstrip("/")
    return f"{base}/reset-password?token={quote(token, safe='')}"


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _eligible(user: User) -> bool:
    """Accounts that must never receive a reset link.

    The shared demo account has a published password and no mailbox, and every
    ``.test`` address (including ``demo@shelfmonitor.test``) is unreachable by design.
    Handing either a token would either mail nobody or let anyone reset a public
    account by guessing its address.
    """
    return not user.mfa_exempt and not user.email.lower().endswith(".test")


def request_reset(email: str, ip: str) -> None:
    """Look `email` up and mail a reset link when the account is eligible.

    Runs as a background task, so it opens its own session: the request-scoped session
    may already be closed, and holding one open across the response would keep a
    transaction alive for the duration of an SMTP/HTTP send.

    Never raises: a background task's exception would be logged by the server with no
    way to tell the caller anything, and the endpoint has already answered 202.
    """
    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        if user is None:
            # Logged without a user id on purpose; this line must never be the reason
            # the response differs in shape from a known address.
            logger.info("auth: password reset requested unknown_address ip=%s", ip)
            return

        if not _eligible(user):
            logger.info("auth: password reset skipped exempt user_id=%s ip=%s", user.id, ip)
            return

        now = utcnow()
        latest = (
            db.query(PasswordResetToken)
            .filter(PasswordResetToken.user_id == user.id)
            .order_by(PasswordResetToken.id.desc())
            .first()
        )
        created = as_utc(latest.created_at) if latest is not None else None
        if created is not None:
            elapsed = (now - created).total_seconds()
            if elapsed < settings.PASSWORD_RESET_COOLDOWN_SECONDS:
                logger.info(
                    "auth: password reset cooldown user_id=%s ip=%s", user.id, ip
                )
                return

        # One working link at a time: an older email stops working the moment a newer
        # one is requested, so every request does not widen the window.
        db.query(PasswordResetToken).filter(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used_at.is_(None),
        ).update({PasswordResetToken.used_at: now}, synchronize_session=False)

        token = new_token()
        db.add(
            PasswordResetToken(
                user_id=user.id,
                token_hash=hash_token(token),
                expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES),
                created_at=now,
                requested_ip=ip,
            )
        )
        db.commit()

        link = reset_link(token)
        delivered = email_service.send_password_reset(
            user.email, link, expires_minutes=settings.PASSWORD_RESET_TTL_MINUTES
        )
        logger.info(
            "auth: password reset requested user_id=%s delivered=%s ip=%s",
            user.id,
            bool(delivered),
            ip,
        )

        if not delivered:
            if settings.DEV_LOG_VERIFICATION_CODES and email_service.transport() == "none":
                # Development aid only, and only with no transport configured at all:
                # an operator with a working relay never gets a token in the logs.
                logger.warning(
                    "DEV_LOG_VERIFICATION_CODES is on and no email transport is "
                    "configured; the password reset link for %s is %s",
                    user.email,
                    link,
                )
            else:
                logger.warning(
                    "auth: could not deliver a password reset email to user_id=%s",
                    user.id,
                )
    except Exception:  # noqa: BLE001 - a background task must not propagate
        logger.exception("auth: password reset request failed (ip=%s)", ip)
        db.rollback()
    finally:
        db.close()


def reset_with_token(
    db: Session, token: str, new_password: str, ip: str
) -> Tuple[str, Optional[int]]:
    """Apply `token` to set `new_password`. Returns (result, user_id).

    Every rejection is the same ``RESULT_INVALID`` so the caller can answer one generic
    message; the specific reason is logged with the user id (when known) and the IP.

    ``password_changed_at`` is truncated to whole seconds because JWT ``iat`` claims are
    second-granular: a token minted in the same second as the change cannot be told
    apart from one minted just after it, and rejecting both would break the first login
    after a reset.
    """
    digest = hash_token(token)
    row = (
        db.query(PasswordResetToken)
        .filter(PasswordResetToken.token_hash == digest)
        .first()
    )
    # The indexed lookup already matched, but comparing again in constant time means
    # the code is also safe if it is ever changed to a scan.
    if row is None or not hmac.compare_digest(digest, row.token_hash):
        logger.info("auth: password reset failed reason=unknown_token ip=%s", ip)
        return RESULT_INVALID, None

    now = utcnow()
    if row.used_at is not None:
        logger.info(
            "auth: password reset failed reason=used user_id=%s ip=%s", row.user_id, ip
        )
        return RESULT_INVALID, None
    if as_utc(row.expires_at) <= now:
        logger.info(
            "auth: password reset failed reason=expired user_id=%s ip=%s",
            row.user_id,
            ip,
        )
        return RESULT_INVALID, None

    user = db.get(User, row.user_id)
    if user is None:
        logger.info(
            "auth: password reset failed reason=missing_user user_id=%s ip=%s",
            row.user_id,
            ip,
        )
        return RESULT_INVALID, None

    user.hashed_password = hash_password(new_password)
    user.password_changed_at = now.replace(microsecond=0)
    # Proving control of the mailbox also proves the address: an unverified account that
    # resets its password is usable immediately rather than stuck behind a signup code.
    user.email_verified = True
    # A reset is a legitimate way out of a failed-login lockout. MFA state, its lockout
    # and the recovery codes are deliberately untouched: the password is not a way to
    # weaken the second factor.
    user.failed_login_attempts = 0
    user.login_locked_until = None

    row.used_at = now
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.id != row.id,
        PasswordResetToken.used_at.is_(None),
    ).update({PasswordResetToken.used_at: now}, synchronize_session=False)
    db.commit()

    logger.info("auth: password reset ok user_id=%s ip=%s", user.id, ip)

    # Never raises (see email_service); a mail outage must not fail the reset itself.
    email_service.send_password_changed_notice(user.email)
    return RESULT_OK, user.id
