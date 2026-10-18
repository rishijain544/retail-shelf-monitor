"""
backend/app/api/v1/endpoints/auth.py

Account creation, login, and "who am I".

POST /auth/signup   Create a Store and its owner User in one transaction;
                    returns a JWT access token.
POST /auth/login    Verify an email/password pair; returns a JWT access token.
GET  /auth/me       Return the current user (id, email, store_id) for a valid
                    token — used by the frontend to check "am I logged in".

No existing endpoint is protected yet; that is a separate, later change.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.auth_deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.models.store import Store
from app.models.user import User
from app.schemas.auth import AuthTokenResponse, LoginRequest, SignupRequest, UserResponse

router = APIRouter()

# A real bcrypt hash that matches nothing, verified against when the email is
# unknown so a failed login costs the same time whether or not the account
# exists (no user enumeration by response time).
_DUMMY_HASH = hash_password("not-a-real-password")


def _token_response(user: User) -> AuthTokenResponse:
    """Build the shared signup/login response for `user`."""
    return AuthTokenResponse(
        access_token=create_access_token(user.id),
        token_type="bearer",
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserResponse.model_validate(user),
    )


@router.post(
    "/signup",
    response_model=AuthTokenResponse,
    summary="Create a store and its owner account",
    status_code=status.HTTP_201_CREATED,
)
def signup(payload: SignupRequest, db: Session = Depends(get_db)) -> AuthTokenResponse:
    """
    Register a new shop.

    Creates the Store and its owning User together — if either insert fails,
    neither is kept. Returns an access token so the caller is logged in
    immediately (no separate /auth/login round trip).
    """
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
    return _token_response(user)


@router.post(
    "/login",
    response_model=AuthTokenResponse,
    summary="Log in with email and password",
)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> AuthTokenResponse:
    """Verify credentials and return an access token."""
    user = db.query(User).filter(User.email == payload.email).first()

    if user is None:
        verify_password(payload.password, _DUMMY_HASH)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _token_response(user)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the authenticated user",
)
def read_current_user(current_user: User = Depends(get_current_user)) -> UserResponse:
    """Return the account behind the request's bearer token."""
    return current_user
