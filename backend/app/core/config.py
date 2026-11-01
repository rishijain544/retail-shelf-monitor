import logging
import os
from typing import Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Environments where a missing or weak SECRET_KEY must abort startup.
_STRICT_ENVIRONMENTS = {"production", "prod", "staging"}

# Environments where a SQLite DATABASE_URL cannot possibly be right. "docker" is set
# by docker-compose.yml for the backend service; production/staging always point at a
# managed database. See _reject_sqlite_in_container for the failure this prevents.
_SQLITE_FORBIDDEN_ENVIRONMENTS = _STRICT_ENVIRONMENTS | {"docker"}

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

    # Bring the database schema up to date at startup (see app/services/schema.py).
    # On by default and deliberately NOT gated on ENVIRONMENT: a managed host that
    # starts uvicorn without the container ENTRYPOINT never runs `alembic upgrade head`,
    # and a deployment created outside render.yaml has no ENVIRONMENT=production to
    # trigger it — which is exactly how the sign-in page ended up answering 500
    # "Internal server error" (`relation "users" does not exist`). It is a no-op once
    # the database is at head. The test suite turns it off (tests/conftest.py) because
    # every test builds its own throwaway schema.
    AUTO_MIGRATE: bool = True

    # Create the demo account advertised on the sign-in page when it is missing.
    # Unset (the default) means "yes outside production/staging" — the sign-in page
    # always shows the demo credentials, so a development-like deployment has to have
    # that row or its demo button answers 401. Set it to "false" to keep a
    # known-password account off any deployment, or "true" to have one in production.
    SEED_DEMO_ACCOUNT: Optional[bool] = None

    # Browser origins allowed to call the API. Comma-separated, e.g.
    #   CORS_ORIGINS=https://shelf-monitor.vercel.app
    # The default "*" mirrors the old hardcoded policy and is development-only: with
    # credentials enabled a wildcard makes every site able to call the API as the
    # logged-in user. Set this to the deployed frontend origin in production.
    CORS_ORIGINS: str = "*"
    # Optional regex for origins that change per deploy — Vercel preview URLs look like
    # https://shelf-monitor-git-branch-user.vercel.app. Example:
    #   CORS_ORIGIN_REGEX=https://.*\.vercel\.app
    CORS_ORIGIN_REGEX: Optional[str] = None

    @property
    def cors_origins(self) -> list[str]:
        """Parse CORS_ORIGINS into the list Starlette's CORSMiddleware expects."""
        raw = (self.CORS_ORIGINS or "").strip()
        if not raw or raw == "*":
            return ["*"]
        return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]

    # Auth — secret used to sign JWT access tokens. Read from the SECRET_KEY
    # env var. There is intentionally NO hardcoded default: a committed default
    # would let anyone forge tokens against a deployment that forgot to set it.
    # In production-like environments it is required (see below); in development
    # security.py falls back to a random per-process key so local work still runs.
    SECRET_KEY: Optional[str] = None

    # How long an issued access token stays valid.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    # Public origin of the dashboard, used only to build links inside emails (today the
    # password-reset link). Never derived from the request: behind Render/Vercel the
    # Host/X-Forwarded-* headers are proxy-controlled, and a reset link pointing at an
    # attacker's host would hand them the token.
    FRONTEND_BASE_URL: str = "http://localhost:5173"
    # How long an emailed password-reset link stays valid, and the per-account cooldown
    # between reset requests (a second request inside it is silently ignored).
    PASSWORD_RESET_TTL_MINUTES: int = 30
    PASSWORD_RESET_COOLDOWN_SECONDS: int = 60

    # Multi-factor authentication.
    #
    # MFA_ENCRYPTION_KEY is a Fernet key used to encrypt each account's TOTP secret
    # at rest; a leaked database dump must not hand over authenticator seeds. It is
    # deliberately separate from SECRET_KEY (rotating the JWT key must not orphan
    # every enrolled authenticator). Generate one with:
    #   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Required in production/staging, like SECRET_KEY. Outside those environments it
    # falls back to a key derived from SECRET_KEY and warns loudly on every boot, so
    # local development keeps working without a second secret to generate.
    MFA_ENCRYPTION_KEY: Optional[str] = None
    # Shown as the account label in the authenticator app ("ShelfMonitor: you@shop.com").
    MFA_ISSUER_NAME: str = "ShelfMonitor"
    # How long a signup's emailed verification code stays valid.
    VERIFICATION_CODE_TTL_MINUTES: int = 10
    # Wrong guesses allowed per verification code / per MFA enrolment before the code
    # is burned and 5 wrong codes lock an account for LOCKOUT_MINUTES.
    MAX_CODE_ATTEMPTS: int = 5
    # Length of a temporary login/MFA lockout after MAX_CODE_ATTEMPTS failures.
    LOCKOUT_MINUTES: int = 15
    # Development aid: print a signup verification code to the log when no email
    # transport is configured at all. Never logs a code when mail can be sent, and
    # never logs the code for an existing deployment that turns this on by accident —
    # it is off by default and documented as local-only.
    DEV_LOG_VERIFICATION_CODES: bool = False

    @field_validator("RESEND_API_KEY", "ALERT_EMAIL_TO", "SMTP_HOST", "SMTP_USER", "SMTP_PASSWORD", "SMTP_FROM", "EMAIL_FROM", "EMAIL_API_URL", "MFA_ENCRYPTION_KEY", mode="before")
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

    @property
    def seed_demo_account(self) -> bool:
        """Whether to create the demo account, applying the not-in-production default."""
        if self.SEED_DEMO_ACCOUNT is not None:
            return self.SEED_DEMO_ACCOUNT
        return not self.IS_STRICT_ENVIRONMENT

    @model_validator(mode="after")
    def _reject_sqlite_in_container(self) -> "Settings":
        """Refuse a SQLite DATABASE_URL where no SQLite file can exist.

        The backend service bind-mounts ./backend into the image, so backend/.env is
        present at /app/.env and pydantic-settings loads it inside the container too. A
        local `DATABASE_URL=sqlite:///d:/.../shelf_monitoring.db` therefore overrode the
        Postgres service and the container crash-looped with
        `sqlite3.OperationalError: unable to open database file` — a host path that does
        not exist in the image, reported by alembic or the engine without naming the
        variable at fault. Failing here names the culprit and the fix instead.
        """
        url = (self.DATABASE_URL or "").strip()
        if not url.lower().startswith("sqlite"):
            return self
        if self.ENVIRONMENT.strip().lower() not in _SQLITE_FORBIDDEN_ENVIRONMENTS:
            return self
        raise RuntimeError(
            f"DATABASE_URL is SQLite ('{url.split('?')[0]}') but ENVIRONMENT="
            f"'{self.ENVIRONMENT}' cannot use a SQLite file. Inside the container this "
            "value comes from backend/.env, which is bind-mounted into the image and "
            "holds a host path. docker-compose.yml pins DATABASE_URL to the db service; "
            "if you run the image outside compose, unset DATABASE_URL (or point it at "
            "postgresql://<user>:<password>@<host>:<port>/<dbname>) so the POSTGRES_* "
            "values build the connection instead."
        )

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

    @model_validator(mode="after")
    def _check_mfa_key(self) -> "Settings":
        """Refuse to start with a missing or malformed TOTP encryption key.

        A valid Fernet key is required rather than optional-on-first-use: without one
        every enrolled authenticator would be unreadable and each affected account
        would need scripts/reset_mfa.py. Failing at boot is loud and fixable; failing
        at the first MFA login is neither.
        """
        key = (self.MFA_ENCRYPTION_KEY or "").strip()
        if not key:
            if not self.IS_STRICT_ENVIRONMENT:
                # security.py derives one from SECRET_KEY and warns on every boot.
                return self
            raise RuntimeError(
                f"MFA_ENCRYPTION_KEY must be set when ENVIRONMENT='{self.ENVIRONMENT}'. "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; "
                "print(Fernet.generate_key().decode())\" and put it in backend/.env "
                "(never commit it). Losing it makes every enrolled authenticator unreadable."
            )

        # An operator-supplied key must be usable; silently falling back to the derived
        # development key would encrypt secrets with something than the next deploy will
        # not be able to reproduce.
        from cryptography.fernet import Fernet  # local import: keeps config import cheap

        try:
            Fernet(key.encode())
        except (ValueError, TypeError) as exc:
            raise RuntimeError(
                "MFA_ENCRYPTION_KEY is not a valid Fernet key. Generate a new one with: "
                "python -c \"from cryptography.fernet import Fernet; "
                f"print(Fernet.generate_key().decode())\" (reason: {exc})"
            ) from exc
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
