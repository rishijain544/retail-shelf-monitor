"""
backend/app/core/auth_deps.py

FastAPI dependencies for authenticated routes.

`get_current_user` resolves the `Authorization: Bearer <token>` header into a
User row, or raises a 401. It is not yet applied to the existing endpoints —
that happens in the next step of the auth rollout.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import decode_access_token
from app.models.camera import Camera
from app.models.user import User

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


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Return the User identified by the request's bearer token, else 401."""
    if credentials is None or not credentials.credentials:
        raise _unauthorized("Not authenticated.")

    payload = decode_access_token(credentials.credentials)
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
    return user


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
