"""
backend/app/api/v1/endpoints/auth.py

Account creation, sign-in, and factor enrolment.

The two rules this module exists to enforce:

1. **An address must be proven before it is a login.** ``POST /auth/signup`` returns
   ``202`` with no token at all and mails a 6-digit code; only
   ``POST /auth/verify-email`` with that code sets ``email_verified``.
2. **A password alone is not a session.** After a correct password, an enrolled
   account gets a 5-minute ``mfa_challenge`` token and must present a TOTP or recovery
   code to ``POST /auth/mfa/verify``; an unenrolled account gets a 15-minute
   ``mfa_setup`` token and must finish ``/auth/mfa/setup/start`` + ``.../confirm``.
   Only then is a real ``access`` token issued.

Both intermediate tokens are signed JWTs with an explicit ``purpose`` claim and are
refused by ``get_current_user``, so they cannot be used anywhere but their own
endpoint (see app/core/auth_deps.py).

Sign-in failures are deliberately indistinguishable: an unknown address and a wrong
password both answer ``401 {"detail": "Invalid email or password."}``, and the unknown
case still runs a bcrypt verification so response time does not reveal account
existence. There is no endpoint that disables MFA — a session, however obtained, must
not be able to remove a second factor. ``scripts/reset_mfa.py`` is the operator escape
hatch.
"""

from __future__ import annotations

import logging
import math
from datetime import timedelta
from typing import Optional, Union

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_deps import (
    get_current_user,
    get_mfa_setup_user,
    token_invalidated_by_password_change,
)
from app.core.clock import as_utc, utcnow
from app.core.config import settings
from app.core.database import get_db
from app.core.security import (
    MFA_CHALLENGE_EXPIRE_MINUTES,
    MFA_SETUP_EXPIRE_MINUTES,
    PURPOSE_MFA_CHALLENGE,
    create_access_token,
    create_mfa_challenge_token,
    create_mfa_setup_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.models.store import Store
from app.models.user import User
from app.schemas.auth import (
    AuthTokenResponse,
    ForgotPasswordRequest,
    LoginRequest,
    MfaChallengeResponse,
    MfaSetupConfirmRequest,
    MfaSetupConfirmResponse,
    MfaSetupRequiredResponse,
    MfaSetupStartResponse,
    MfaVerifyRequest,
    RecoveryCodesResponse,
    RegenerateRecoveryCodesRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    PasswordResetResponse,
    SignupPendingResponse,
    SignupRequest,
    StatusResponse,
    UserResponse,
    VerifyEmailRequest,
    VerifyEmailResponse,
)
from app.services import mfa, password_reset, verification

logger = logging.getLogger(__name__)

router = APIRouter()

# POST /auth/login can end in three different successful shapes (a session, an MFA
# challenge, or an enrolment requirement); the union documents all of them.
LoginResult = Union[AuthTokenResponse, MfaChallengeResponse, MfaSetupRequiredResponse]

# A real bcrypt hash that matches nothing, verified against when the email is
# unknown so a failed login costs the same time whether or not the account
# exists (no user enumeration by response time).
_DUMMY_HASH = hash_password("not-a-real-password")

_GENERIC_LOGIN_ERROR = "Invalid email or password."
_GENERIC_CODE_ERROR = (
    "That verification code is not valid or has expired. Request a new one."
)
_GENERIC_MFA_ERROR = (
    "That code is not valid. Use the current code from your authenticator app, "
    "or one of your recovery codes."
)
# One body for every /forgot-password caller, known address or not. Anything that
# varies here (message, status, timing) is an account-existence oracle.
_GENERIC_RESET_MESSAGE = (
    "If that address has an account, a reset link is on its way."
)
_GENERIC_RESET_TOKEN_ERROR = (
    "That reset link is invalid or has expired. Request a new one."
)


def _client_ip(request: Request) -> str:
    """Best-effort client address for auth logs (never used for authorisation).

    Render/Vercel terminate TLS in front of the app, so the socket peer is a proxy;
    the first X-Forwarded-For entry is the visitor. Only ever logged, never trusted.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client is not None else "unknown"


def _error(
    status_code: int,
    message: str,
    code: str,
    *,
    headers: Optional[dict[str, str]] = None,
) -> JSONResponse:
    """A failure body that is both readable and machine-checkable.

    ``detail`` stays a plain string so existing clients (which surface ``detail``)
    keep working, while ``code`` lets a client branch on the reason without matching
    message text.
    """
    return JSONResponse(
        status_code=status_code, content={"detail": message, "code": code}, headers=headers
    )


def _unauthorized(message: str, code: str) -> JSONResponse:
    return _error(
        status.HTTP_401_UNAUTHORIZED,
        message,
        code,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _seconds_left(until) -> int:
    """Whole seconds until `until`, or 0 when it is unset/past."""
    moment = as_utc(until)
    if moment is None:
        return 0
    return max(0, int((moment - utcnow()).total_seconds()))


def _minutes_left(seconds: int) -> int:
    return max(1, math.ceil(seconds / 60))


def _token_response(user: User) -> AuthTokenResponse:
    """Build the shared sign-in response for `user` (a full session)."""
    return AuthTokenResponse(
        access_token=create_access_token(user.id),
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


# ─── Signup + email verification ──────────────────────────────────────────────


@router.post(
    "/signup",
    response_model=SignupPendingResponse,
    summary="Create a store and its owner account, then email a verification code",
    status_code=status.HTTP_202_ACCEPTED,
)
def signup(
    payload: SignupRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> SignupPendingResponse:
    """
    Register a new shop.

    Creates the Store and its owning User together — if either insert fails, neither
    is kept. The account is created *unverified* and no token is returned: an address
    becomes a usable login only after the mailed code is confirmed, so a typo'd or
    someone else's address cannot sign itself up.
    """
    ip = _client_ip(request)

    existing = db.query(User).filter(User.email == payload.email).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    store = Store(name=payload.store_name, location=payload.store_location)
    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        email_verified=False,
        # Never derived from the request body: Pydantic ignores unknown fields, so a
        # client cannot smuggle mfa_exempt (or mfa_enabled) into signup.
        mfa_enabled=False,
        mfa_exempt=False,
    )

    try:
        db.add(store)
        db.flush()  # assigns store.id without committing
        user.store_id = store.id
        db.add(user)
        db.commit()  # one transaction covering both rows
    except IntegrityError:
        # Unique-email violation from a concurrent signup that slipped past the
        # check above; roll back so the Store insert is undone too.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )

    db.refresh(user)
    logger.info("auth: signup user_id=%s ip=%s", user.id, ip)

    delivered = verification.issue_and_send(db, user, store_name=store.name)
    if delivered:
        logger.info("auth: verification sent user_id=%s ip=%s", user.id, ip)

    return SignupPendingResponse(
        email=user.email,
        expires_in=settings.VERIFICATION_CODE_TTL_MINUTES * 60,
    )


@router.post(
    "/verify-email",
    response_model=VerifyEmailResponse,
    summary="Confirm a signup address with the emailed code",
)
def verify_email(
    payload: VerifyEmailRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> VerifyEmailResponse:
    """Consume the emailed code and issue the token that starts MFA enrolment.

    A wrong code and an unknown address answer the same 400 so this endpoint cannot be
    used to discover which addresses have accounts.
    """
    ip = _client_ip(request)
    user = db.query(User).filter(User.email == payload.email).first()
    if user is None:
        logger.info("auth: verify failed unknown_address ip=%s", ip)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_GENERIC_CODE_ERROR
        )

    result = verification.verify(db, user, payload.code)
    if result != verification.RESULT_OK:
        logger.info(
            "auth: verify failed user_id=%s reason=%s ip=%s", user.id, result, ip
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=_GENERIC_CODE_ERROR
        )

    logger.info("auth: verify ok user_id=%s ip=%s", user.id, ip)
    return VerifyEmailResponse(
        setup_token=create_mfa_setup_token(user.id),
        expires_in=MFA_SETUP_EXPIRE_MINUTES * 60,
    )


@router.post(
    "/resend-verification",
    response_model=StatusResponse,
    summary="Send a new verification code (60 second cooldown)",
    status_code=status.HTTP_202_ACCEPTED,
)
def resend_verification(
    payload: ResendVerificationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> StatusResponse:
    """Mail a fresh code, at most once per 60 seconds per account.

    An unknown address gets the same 202 as a known one, so the endpoint does not
    become an account oracle.
    """
    ip = _client_ip(request)
    user = db.query(User).filter(User.email == payload.email).first()

    if user is None:
        logger.info("auth: resend for unknown_address ip=%s", ip)
        return StatusResponse(status="verification_sent")

    if user.email_verified:
        return StatusResponse(
            status="already_verified",
            message="This address is already verified. Sign in to continue.",
        )

    wait = verification.seconds_until_resend_allowed(db, user)
    if wait > 0:
        logger.info(
            "auth: resend cooldown user_id=%s wait=%ss ip=%s", user.id, wait, ip
        )
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"A code was just sent. Try again in {wait} seconds.",
            "resend_cooldown",
            headers={"Retry-After": str(wait)},
        )

    delivered = verification.issue_and_send(db, user)
    logger.info(
        "auth: verification resent user_id=%s delivered=%s ip=%s",
        user.id,
        delivered,
        ip,
    )
    return StatusResponse(
        status="verification_sent",
        message=None
        if delivered
        else "The code was created but the email could not be sent. Try again shortly.",
    )


# ─── Login ────────────────────────────────────────────────────────────────────


def _reset_requested_response() -> StatusResponse:
    """The one response /forgot-password ever returns."""
    return StatusResponse(status="reset_requested", message=_GENERIC_RESET_MESSAGE)


@router.post(
    "/forgot-password",
    response_model=StatusResponse,
    summary="Email a password reset link (same answer for every address)",
    status_code=status.HTTP_202_ACCEPTED,
)
def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    background_tasks: BackgroundTasks,
) -> StatusResponse:
    """Request a reset link. The answer never reveals whether the account exists.

    The lookup and the send run in a background task, so the response is sent before
    any work that depends on the address — an unknown address cannot be spotted by a
    faster reply. Only the in-memory per-IP budget is consulted synchronously, and a
    limited caller gets the same 202 (logged), not a 429: a different status here would
    itself be an oracle about this client, not about the account.
    """
    ip = _client_ip(request)
    if password_reset.allow("forgot", ip, limit=password_reset.FORGOT_MAX_PER_IP):
        background_tasks.add_task(password_reset.request_reset, payload.email, ip)
    else:
        logger.info("auth: password reset rate limited ip=%s", ip)
    return _reset_requested_response()


@router.post(
    "/reset-password",
    response_model=PasswordResetResponse,
    summary="Set a new password with an emailed reset link",
)
def reset_password(
    payload: ResetPasswordRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> PasswordResetResponse:
    """Apply a reset link: new password, all older sessions invalidated, no login.

    A successful reset returns a status only — no access, challenge or setup token —
    because a reset proves the mailbox, not a second factor. Signing in is a separate
    step and still asks for the authenticator code on an enrolled account.
    """
    ip = _client_ip(request)
    if not password_reset.allow("reset", ip, limit=password_reset.RESET_MAX_PER_IP):
        logger.info("auth: password reset apply rate limited ip=%s", ip)
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many reset attempts. Try again later.",
            "password_reset_rate_limited",
            headers={"Retry-After": str(password_reset.IP_WINDOW_SECONDS)},
        )

    result, _user_id = password_reset.reset_with_token(
        db, payload.token, payload.new_password, ip
    )
    if result != password_reset.RESULT_OK:
        # reset_with_token already logged the reason with the user id (when known) and
        # the IP; the caller gets one message for unknown, expired and reused links.
        return _error(
            status.HTTP_400_BAD_REQUEST,
            _GENERIC_RESET_TOKEN_ERROR,
            "reset_token_invalid",
        )

    return PasswordResetResponse()


@router.post(
    "/login",
    response_model=LoginResult,
    summary="Start a sign-in: session, MFA challenge, or MFA enrolment",
)
def login(
    payload: LoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> LoginResult:
    """
    Verify the password, then decide what the account still needs.

    * exempt account (the shared demo login) → access token
    * MFA enrolled → 5-minute challenge token, ``{"status": "mfa_required"}``
    * MFA not enrolled → 15-minute setup token, ``{"status": "mfa_setup_required"}``
    * unverified address → 403 ``{"code": "email_unverified"}``

    Wrong credentials always answer the same 401, and repeated failures lock the
    account for ``LOCKOUT_MINUTES``.
    """
    ip = _client_ip(request)
    user = db.query(User).filter(User.email == payload.email).first()

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        logger.info("auth: login failed unknown_address ip=%s", ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Checked before the password so a locked account cannot be used as a password
    # oracle, and so a correct password cannot reset the counter early.
    locked_for = _seconds_left(user.login_locked_until)
    if locked_for > 0:
        logger.info("auth: login locked user_id=%s ip=%s", user.id, ip)
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many failed sign-in attempts. Try again in "
            f"{_minutes_left(locked_for)} minute(s).",
            "account_locked",
            headers={"Retry-After": str(locked_for)},
        )

    if not verify_password(payload.password, user.hashed_password):
        user.failed_login_attempts = (user.failed_login_attempts or 0) + 1
        now_locked = user.failed_login_attempts >= settings.MAX_CODE_ATTEMPTS
        if now_locked:
            user.login_locked_until = utcnow() + timedelta(
                minutes=settings.LOCKOUT_MINUTES
            )
            # Reset so that after the lockout expires the account gets a full set of
            # attempts again, instead of relocking on the very next typo.
            user.failed_login_attempts = 0
        db.commit()
        logger.info(
            "auth: login failed user_id=%s ip=%s locked=%s", user.id, ip, now_locked
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_LOGIN_ERROR,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Password is correct from here on.
    user.failed_login_attempts = 0
    user.login_locked_until = None
    db.commit()

    if not user.email_verified:
        logger.info("auth: login blocked unverified user_id=%s ip=%s", user.id, ip)
        return _error(
            status.HTTP_403_FORBIDDEN,
            "Your email address is not verified yet. Enter the code we emailed you, "
            "or request a new one.",
            "email_unverified",
        )

    if user.mfa_exempt:
        # The public demo account: no mailbox, no authenticator, published password.
        # Exemption is only settable from a script, never from an API call.
        logger.info("auth: login ok user_id=%s ip=%s mfa=exempt", user.id, ip)
        return _token_response(user)

    if user.mfa_enabled:
        logger.info("auth: login password ok user_id=%s ip=%s mfa=challenge", user.id, ip)
        return MfaChallengeResponse(
            challenge_token=create_mfa_challenge_token(user.id),
            expires_in=MFA_CHALLENGE_EXPIRE_MINUTES * 60,
        )

    logger.info("auth: login password ok user_id=%s ip=%s mfa=enrolment", user.id, ip)
    return MfaSetupRequiredResponse(
        setup_token=create_mfa_setup_token(user.id),
        expires_in=MFA_SETUP_EXPIRE_MINUTES * 60,
    )


# ─── MFA enrolment ────────────────────────────────────────────────────────────


@router.post(
    "/mfa/setup/start",
    response_model=MfaSetupStartResponse,
    summary="Begin MFA enrolment: issue an authenticator secret",
)
def mfa_setup_start(
    request: Request,
    current_user: User = Depends(get_mfa_setup_user),
    db: Session = Depends(get_db),
) -> MfaSetupStartResponse:
    """Store a new pending secret and return it once, for the authenticator app.

    Calling this again replaces the pending secret — the previous one was never
    confirmed, so leaving several alive would only widen the window.
    """
    ip = _client_ip(request)
    if current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled on this account.",
        )

    secret = mfa.generate_secret()
    mfa.store_secret(current_user, secret)
    db.commit()
    logger.info("auth: mfa setup started user_id=%s ip=%s", current_user.id, ip)

    return MfaSetupStartResponse(
        secret=secret,
        otpauth_uri=mfa.provisioning_uri(current_user, secret),
        issuer=settings.MFA_ISSUER_NAME,
        account=current_user.email,
    )


@router.post(
    "/mfa/setup/confirm",
    response_model=MfaSetupConfirmResponse,
    summary="Confirm enrolment with a code, then sign in with recovery codes",
)
def mfa_setup_confirm(
    payload: MfaSetupConfirmRequest,
    request: Request,
    current_user: User = Depends(get_mfa_setup_user),
    db: Session = Depends(get_db),
) -> MfaSetupConfirmResponse:
    """Enable MFA, return ten recovery codes (once), and start the session."""
    ip = _client_ip(request)
    if current_user.mfa_enabled:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MFA is already enabled on this account.",
        )

    secret = mfa.read_secret(current_user)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Start the authenticator setup again — there is no pending secret.",
        )

    step = mfa.verify_totp(secret, payload.code, None)
    if step is None:
        logger.info("auth: mfa confirm failed user_id=%s ip=%s", current_user.id, ip)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That code did not match. Check your authenticator app's clock and "
            "enter the current code.",
        )

    current_user.mfa_enabled = True
    current_user.mfa_last_used_step = step
    current_user.failed_mfa_attempts = 0
    current_user.mfa_locked_until = None
    recovery_codes = mfa.generate_recovery_codes(db, current_user)
    db.commit()
    logger.info("auth: mfa enrolled user_id=%s ip=%s", current_user.id, ip)

    token = _token_response(current_user)
    return MfaSetupConfirmResponse(
        **token.model_dump(), recovery_codes=recovery_codes
    )


# ─── MFA verification ─────────────────────────────────────────────────────────


@router.post(
    "/mfa/verify",
    response_model=AuthTokenResponse,
    summary="Finish a sign-in with a TOTP or recovery code",
)
def mfa_verify(
    payload: MfaVerifyRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> AuthTokenResponse:
    """Exchange a challenge token plus a second factor for a real access token.

    A TOTP code must belong to a time step newer than the last accepted one (replay
    protection); a recovery code is consumed on first use. Five wrong codes lock the
    account for ``LOCKOUT_MINUTES``.
    """
    ip = _client_ip(request)

    claims = decode_token(payload.challenge_token, purpose=PURPOSE_MFA_CHALLENGE)
    if not claims:
        logger.info("auth: mfa verify rejected bad_challenge ip=%s", ip)
        return _unauthorized(
            "That sign-in attempt has expired or is invalid. Sign in again.",
            "mfa_challenge_invalid",
        )

    try:
        user_id = int(claims.get("sub"))
    except (TypeError, ValueError):
        return _unauthorized(
            "That sign-in attempt has expired or is invalid. Sign in again.",
            "mfa_challenge_invalid",
        )

    user = db.get(User, user_id)
    if user is None or not user.mfa_enabled or user.mfa_exempt:
        return _unauthorized(
            "That sign-in attempt has expired or is invalid. Sign in again.",
            "mfa_challenge_invalid",
        )

    # A challenge token issued before a password reset must not finish a sign-in: the
    # reset is meant to end every session that existed beforehand.
    if token_invalidated_by_password_change(claims, user):
        logger.info(
            "auth: mfa challenge rejected after password change user_id=%s ip=%s",
            user.id,
            ip,
        )
        return _unauthorized(
            "That sign-in attempt has expired or is invalid. Sign in again.",
            "mfa_challenge_invalid",
        )

    locked_for = mfa.lockout_remaining_seconds(user)
    if locked_for > 0:
        logger.info("auth: mfa locked user_id=%s ip=%s", user.id, ip)
        return _error(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many incorrect codes. Try again in "
            f"{_minutes_left(locked_for)} minute(s), or use a recovery code after that.",
            "mfa_locked",
            headers={"Retry-After": str(locked_for)},
        )

    secret = mfa.read_secret(user)
    step = mfa.verify_totp(secret, payload.code, user.mfa_last_used_step) if secret else None

    if step is None:
        # Not a TOTP code (or the secret is unreadable): try a recovery code. This is
        # also the path that works when the encryption key has been rotated away.
        if not mfa.consume_recovery_code(db, user, payload.code):
            user.failed_mfa_attempts = (user.failed_mfa_attempts or 0) + 1
            now_locked = user.failed_mfa_attempts >= settings.MAX_CODE_ATTEMPTS
            if now_locked:
                user.mfa_locked_until = utcnow() + timedelta(
                    minutes=settings.LOCKOUT_MINUTES
                )
                user.failed_mfa_attempts = 0
            db.commit()
            logger.info(
                "auth: mfa failed user_id=%s ip=%s locked=%s",
                user.id,
                ip,
                now_locked,
            )
            return _unauthorized(_GENERIC_MFA_ERROR, "mfa_code_invalid")
    else:
        user.mfa_last_used_step = step

    user.failed_mfa_attempts = 0
    user.mfa_locked_until = None
    db.commit()
    logger.info("auth: mfa ok user_id=%s ip=%s recovery=%s", user.id, ip, step is None)
    return _token_response(user)


@router.post(
    "/mfa/recovery-codes/regenerate",
    response_model=RecoveryCodesResponse,
    summary="Replace the recovery codes (requires password and current TOTP code)",
)
def regenerate_recovery_codes(
    payload: RegenerateRecoveryCodesRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RecoveryCodesResponse:
    """Issue ten new recovery codes, invalidating every old one.

    Both the password and a current TOTP code are required, so a stolen session on its
    own cannot mint itself a permanent bypass.
    """
    ip = _client_ip(request)

    if not current_user.mfa_enabled or current_user.mfa_exempt:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account does not have MFA enabled.",
        )

    if not verify_password(payload.password, current_user.hashed_password):
        logger.info("auth: recovery regenerate bad_password user_id=%s ip=%s", current_user.id, ip)
        return _unauthorized("That password is not correct.", "invalid_password")

    secret = mfa.read_secret(current_user)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your authenticator secret could not be read. Ask an administrator "
            "to reset MFA for this account.",
        )

    step = mfa.verify_totp(secret, payload.code, current_user.mfa_last_used_step)
    if step is None:
        logger.info("auth: recovery regenerate bad_code user_id=%s ip=%s", current_user.id, ip)
        return _unauthorized(
            "That authenticator code is not valid or has already been used.",
            "invalid_totp_code",
        )

    current_user.mfa_last_used_step = step
    codes = mfa.generate_recovery_codes(db, current_user)
    db.commit()
    logger.info("auth: recovery codes regenerated user_id=%s ip=%s", current_user.id, ip)
    return RecoveryCodesResponse(recovery_codes=codes)


# ─── Current user ─────────────────────────────────────────────────────────────


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the authenticated user",
)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the account behind the request's bearer token.

    Only a full ``access`` token gets here; challenge and setup tokens are rejected by
    the dependency.
    """
    return current_user
