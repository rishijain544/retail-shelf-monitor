from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Deliberately permissive: a non-empty local part, an "@", and a dotted domain.
# (EmailStr would need the extra `email-validator` dependency.)
_EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class _EmailNormalizingModel(BaseModel):
    """Shared email normalisation so lookups and the unique index agree."""

    # mode="before" so trimming/lower-casing happens *before* the pattern and
    # length constraints run — otherwise " Owner@Shop.com " is rejected as
    # malformed instead of being normalised into the existing account.
    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _normalize_email(cls, value):
        return value.strip().lower() if isinstance(value, str) else value


class SignupRequest(_EmailNormalizingModel):
    """Payload for POST /auth/signup — creates a Store and its owner together."""

    store_name: str = Field(min_length=1, max_length=255)
    store_location: Optional[str] = Field(default=None, max_length=255)
    email: str = Field(pattern=_EMAIL_PATTERN, max_length=320)
    password: str = Field(min_length=8, max_length=72)


class LoginRequest(_EmailNormalizingModel):
    """Payload for POST /auth/login."""

    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=72)


class UserResponse(BaseModel):
    """The authenticated user's own record — never includes the password hash."""

    id: int
    email: str
    store_id: int

    model_config = ConfigDict(from_attributes=True)


class AuthTokenResponse(BaseModel):
    """Returned by both signup and login: a bearer token plus who it belongs to."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until the token expires
    user: UserResponse
