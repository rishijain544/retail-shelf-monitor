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
    """The authenticated user's own record — never includes the password hash.

    ``email_verified``/``mfa_enabled`` are exposed so a client can tell a
    half-finished signup from a complete account. The MFA secret, its encryption and
    the lockout counters are never returned.
    """

    id: int
    email: str
    store_id: int
    email_verified: bool = False
    mfa_enabled: bool = False

    model_config = ConfigDict(from_attributes=True)


class AuthTokenResponse(BaseModel):
    """A full bearer token plus who it belongs to (issued only after every factor)."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds until the token expires
    user: UserResponse


class SignupPendingResponse(BaseModel):
    """Returned by signup: the account exists but no session does.

    Deliberately carries no token. Signing in before the emailed code is confirmed is
    a 403, so "signed up" and "logged in" must not be the same event.
    """

    status: str = "verification_required"
    email: str
    expires_in: int  # seconds the emailed code stays valid


class VerifyEmailRequest(_EmailNormalizingModel):
    """Payload for POST /auth/verify-email."""

    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=4, max_length=12)


class VerifyEmailResponse(BaseModel):
    """A confirmed address earns a short-lived token to enrol an authenticator."""

    status: str = "email_verified"
    setup_token: str
    expires_in: int


class ResendVerificationRequest(_EmailNormalizingModel):
    """Payload for POST /auth/resend-verification."""

    email: str = Field(min_length=3, max_length=320)


class MfaChallengeResponse(BaseModel):
    """Password accepted, second factor still required. No access token yet."""

    status: str = "mfa_required"
    challenge_token: str
    expires_in: int


class MfaSetupRequiredResponse(BaseModel):
    """Password accepted, but this account has no authenticator enrolled yet."""

    status: str = "mfa_setup_required"
    setup_token: str
    expires_in: int


class MfaVerifyRequest(BaseModel):
    """Payload for POST /auth/mfa/verify — a TOTP code or a recovery code."""

    challenge_token: str = Field(min_length=10)
    code: str = Field(min_length=6, max_length=40)


class MfaSetupStartResponse(BaseModel):
    """The one-time display of a pending authenticator secret."""

    secret: str  # base32, for manual entry
    otpauth_uri: str  # otpauth:// URI, for a QR code drawn by the client
    issuer: str
    account: str


class MfaSetupConfirmRequest(BaseModel):
    """Payload for POST /auth/mfa/setup/confirm."""

    code: str = Field(min_length=6, max_length=10)


class MfaSetupConfirmResponse(AuthTokenResponse):
    """Enrolment succeeded: a session plus the recovery codes, shown once."""

    recovery_codes: list[str]


class RegenerateRecoveryCodesRequest(BaseModel):
    """Payload for POST /auth/mfa/recovery-codes/regenerate.

    Both factors are required: a stolen session alone must not be able to mint itself
    a permanent way back in.
    """

    password: str = Field(min_length=1, max_length=72)
    code: str = Field(min_length=6, max_length=10)


class RecoveryCodesResponse(BaseModel):
    """Freshly generated recovery codes. These are never retrievable again."""

    status: str = "recovery_codes_regenerated"
    recovery_codes: list[str]


class StatusResponse(BaseModel):
    """A machine-readable outcome plus a message safe to show a user."""

    status: str
    message: Optional[str] = None
