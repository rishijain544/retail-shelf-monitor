import logging
import os
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Environments where a missing or weak SECRET_KEY must abort startup.
_STRICT_ENVIRONMENTS = {"production", "prod", "staging"}

# Absolute path to the backend/ directory (config.py is at backend/app/core/config.py)
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

class Settings(BaseSettings):
    PROJECT_NAME: str = "Retail Shelf Monitoring & Restocking Assistant"
    API_V1_STR: str = "/api/v1"

    # Optional direct Database URL (e.g. for local SQLite or customized connection string)
    DATABASE_URL: Optional[str] = None

    # PostgreSQL configuration
    POSTGRES_SERVER: str = "localhost"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "shelf_monitoring"
    POSTGRES_PORT: int = 5432

    # Anthropic API
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_WORKSPACE_ID: Optional[str] = None

    # File uploads: captured shelf images are saved here and served at /uploads
    UPLOAD_DIR: str = os.path.join(_BACKEND_DIR, "uploads")

    # Slack alerting — Incoming Webhook URL used by slack_service to post alerts.
    SLACK_WEBHOOK_URL: Optional[str] = None

    # Email alerting — email_service mails the same high-urgency alerts to the store's
    # login address(es). Two transports, picked in this order:
    #   1. RESEND_API_KEY — HTTPS API, ONE key and nothing else (no SMTP credentials).
    #      From defaults to Resend's onboarding@resend.dev sandbox sender, which may only
    #      mail the address the Resend account was created with — enough for a store's
    #      own login address. Verify a domain and set EMAIL_FROM to mail anyone.
    #   2. SMTP_* — a plain mail relay (Gmail App Password, Office 365, internal relay).
    # With neither set, delivery is skipped (logged) and the pipeline still runs; the
    # alert row records sent_to_email=false.
    #   Gmail:    SMTP_HOST=smtp.gmail.com SMTP_PORT=587 SMTP_USE_TLS=true
    #             (use an App Password, not the account password)
    #   Outlook:  SMTP_HOST=smtp.office365.com SMTP_PORT=587 SMTP_USE_TLS=true
    #   implicit TLS relay on 465: set SMTP_PORT=465 and SMTP_USE_SSL=true
    # Resend API key (starts with "re_"). When set, email is sent over their HTTPS API
    # instead of SMTP — no SMTP username/password needed.
    RESEND_API_KEY: Optional[str] = None
    # From: address for the API transport, e.g. "ShelfMonitor <alerts@yourdomain.com>".
    # Defaults to Resend's sandbox sender when unset.
    EMAIL_FROM: Optional[str] = None
    # Comma-separated list of addresses that receive ALL alert emails (test and real).
    # When set, these override the per-store login emails. Required for Resend's free
    # tier, which only delivers to the address the Resend account was created with.
    # When unset, alert emails go to the login address of every account in the store.
    ALERT_EMAIL_TO: Optional[str] = None
    # Endpoint for the API transport. Override only for a proxy/mirror or local testing.
    EMAIL_API_URL: str = "https://api.resend.com/emails"

    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    # From: address. Falls back to SMTP_USER when unset.
    SMTP_FROM: Optional[str] = None
    # STARTTLS (587). Ignored when SMTP_USE_SSL is on.
    SMTP_USE_TLS: bool = True
    # Implicit TLS (465) — wraps the connection before the SMTP greeting.
    SMTP_USE_SSL: bool = False
    # Master switch so an operator can silence email while keeping Slack.
    ALERT_EMAIL_ENABLED: bool = True

    # Deployment environment. "production" (and "staging") turn on the strict
    # configuration checks at the bottom of this class.
    ENVIRONMENT: str = "development"

    # Auth — secret used to sign JWT access tokens. Read from the SECRET_KEY
    # env var. There is intentionally NO hardcoded default: a committed default
    # would let anyone forge tokens against a deployment that forgot to set it.
    # In production-like environments it is required (see below); in development
    # security.py falls back to a random per-process key so local work still runs.
    SECRET_KEY: Optional[str] = None

    # How long an issued access token stays valid.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    @field_validator("RESEND_API_KEY", "ALERT_EMAIL_TO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "EMAIL_FROM", "EMAIL_API_URL", mode="before")
    @classmethod
    def _strip_credentials(cls, value):
        """Tolerate quotes, stray CR and trailing spaces around a pasted credential.

        A key copied out of a provider dashboard (or a CRLF .env) often arrives as
        `"re_abc…"` or with a trailing `\\r`; sent verbatim in an Authorization
        header that is simply an invalid key (HTTP 401) with no hint why.
        """
        if not isinstance(value, str):
            return value
        return value.strip().strip('"').strip("'").strip()

    @property
    def alert_email_recipients(self) -> list[str]:
        """Parsed ALERT_EMAIL_TO list (comma-separated), empty when unset."""
        if not self.ALERT_EMAIL_TO:
            return []
        return [a.strip() for a in self.ALERT_EMAIL_TO.split(",") if a.strip()]

    @property
    def smtp_from_address(self) -> Optional[str]:
        """The From: address to send alerts from (SMTP_FROM, else the SMTP login)."""
        return self.SMTP_FROM or self.SMTP_USER

    @property
    def email_from_address(self) -> str:
        """The From: address the email API sends as (EMAIL_FROM, SMTP identities, sandbox)."""
        return (
            self.EMAIL_FROM
            or self.smtp_from_address
            or "ShelfMonitor <onboarding@resend.dev>"
        )

    @property
    def IS_STRICT_ENVIRONMENT(self) -> bool:
        return self.ENVIRONMENT.strip().lower() in _STRICT_ENVIRONMENTS

    @model_validator(mode="after")
    def _check_auth_secrets(self) -> "Settings":
        """Fail loudly rather than silently signing tokens with a weak secret."""
        if not self.IS_STRICT_ENVIRONMENT:
            return self

        if not self.SECRET_KEY:
            raise RuntimeError(
                f"SECRET_KEY must be set when ENVIRONMENT='{self.ENVIRONMENT}'. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(48))\" "
                "and put it in backend/.env (never commit it)."
            )
        if len(self.SECRET_KEY) < 32:
            raise RuntimeError(
                f"SECRET_KEY is too short ({len(self.SECRET_KEY)} chars) for ENVIRONMENT="
                f"'{self.ENVIRONMENT}'; use at least 32 characters of entropy."
            )
        return self

    @property
    def SQLALCHEMY_DATABASE_URI(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return f"postgresql://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_SERVER}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"

    # Pydantic settings config to load .env file
    # Note: env_file can be a relative path or absolute path. We allow missing files.
    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

settings = Settings()

if not settings.SECRET_KEY:
    # Not fatal outside production — security.py generates a random per-process
    # key instead — but tokens stop working across restarts, so say so once.
    logger.warning(
        "SECRET_KEY is not set (ENVIRONMENT=%s). A random per-process key will be "
        "used for JWT signing, so tokens are invalidated on every restart. Set "
        "SECRET_KEY in backend/.env to keep sessions stable.",
        settings.ENVIRONMENT,
    )

# If we are debugging, it can be useful to print settings:
# print("Loaded database URI:", settings.SQLALCHEMY_DATABASE_URI)
