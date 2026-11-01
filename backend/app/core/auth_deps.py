"""
backend/app/core/auth_deps.py

FastAPI dependencies for authenticated routes.

`get_current_user` resolves the `Authorization: Bearer <token>` header into a
User row, or raises a 401, and it accepts **only** tokens whose `purpose` claim is
`access`. The two factor-limited tokens issued during sign-in have their own
dependencies (`get_mfa_challenge_user`, `get_mfa_setup_user`) which are used by
nothing but the endpoint they belong to; presenting one anywhere else is a 401.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional, Union

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.clock import as_utc
from app.core.security import (
    PURPOSE_ACCESS,
    PURPOSE_MFA_CHALLENGE,
    PURPOSE_MFA_SETUP,
    decode_token,
)
from app.models.camera import Camera
from app.models.user import User

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from app.models.store import Store

# auto_error=False so a missing header produces our own 401 (with a
# WWW-Authenticate header) instead of FastAPI's 403.
bearer_scheme = HTTPBearer(auto_error=False)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _issued_at(payload: dict[str, Any]) -> Optional[datetime]:
    """The token's ``iat`` claim as an aware UTC datetime, or None when unusable."""
    raw = payload.get("iat")
    if raw is None:
        return None
    try:
        return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def token_invalidated_by_password_change(payload: dict[str, Any], user: User) -> bool:
    """True when `payload` must not be honoured for `user` any more.

    Two rules, in one place so every factor-limited token behaves the same:

    * a token without a usable ``iat`` is invalid — there is no way to date it;
    * a reset moves ``password_changed_at`` forward, and any token signed before that
      moment is refused. That is what signs an account out everywhere after a password
      change (access, ``mfa_challenge`` and ``mfa_setup`` tokens alike).

    Reusing this in ``/auth/mfa/verify`` matters because that route decodes its
    challenge token itself instead of going through a dependency.
    """
    issued = _issued_at(payload)
    if issued is None:
        return True
    changed = as_utc(user.password_changed_at)
    if changed is None:
        return False
    return issued < changed


def _user_from_token(
    credentials: Optional[HTTPAuthorizationCredentials], db: Session, purpose: str
) -> User:
    """Resolve a bearer token of exactly `purpose` into its User, else 401.

    The purpose check lives here rather than in each endpoint because every private
    route already goes through this dependency: an `mfa_setup` token accidentally
    handed to /reports is rejected before the handler runs.
    """
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated.")

    payload = decode_token(credentials.credentials, purpose=purpose)
    if not payload:
        raise _unauthorized("Invalid or expired access token.")

    subject = payload.get("sub")
    try:
        user_id = int(subject)
    except (TypeError, ValueError):
        raise _unauthorized("Invalid or expired access token.")

    user = db.get(User, user_id)
    if user is None:
        # Token is well-formed but the account is gone (e.g. deleted store).
        raise _unauthorized("Invalid or expired access token.")
    if token_invalidated_by_password_change(payload, user):
        # A missing/undatable iat, or a token signed before the last password reset.
        logger.info(
            "auth: rejected %s token from before a password change user_id=%s",
            purpose,
            user.id,
        )
        raise _unauthorized("Your session has expired. Sign in again.")
    return user


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Return the User identified by the request's bearer token, else 401."""
    return _user_from_token(credentials, db, PURPOSE_ACCESS)


def get_mfa_challenge_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve an `mfa_challenge` token (correct password, second factor pending).

    Only POST /auth/mfa/verify uses this.
    """
    return _user_from_token(credentials, db, PURPOSE_MFA_CHALLENGE)


def get_mfa_setup_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Resolve an `mfa_setup` token (identity proven, enrolment pending).

    Only the two /auth/mfa/setup/* endpoints use this.
    """
    return _user_from_token(credentials, db, PURPOSE_MFA_SETUP)


def get_current_active_store(user: User = Depends(get_current_user)) -> Store:
    """Return the store the current user belongs to, loading it from DB.

    The ``user.stored_id`` is never trusted from the client — it is always read
    from the loaded User row. If the user no longer has a store (e.g. its row
    was deleted), a 403 is returned.
    """
    if user.store_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is not linked to a store.",
        )
    # Load the store row fresh for every request so its FK integrity is real.
    store = user.store
    if store is None or store.id != user.store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account's store no longer exists.",
        )
    return store


def _assert_camera_belongs_to_user(
    camera_id: int,
    db: Session,
    user: User,
) -> Camera:
    """Raise 403 unless ``camera_id`` belongs to ``user.store_id``; return the camera."""
    camera = db.get(Camera, camera_id)
    if camera is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Camera {camera_id} not found.",
        )
    if camera.store_id != user.store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="That camera does not belong to your store.",
        )
    return camera
