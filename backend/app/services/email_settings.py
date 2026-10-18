"""
backend/app/services/email_settings.py

Load and persist the installation-wide email transport credentials.

Precedence: a saved database row wins over ``RESEND_API_KEY`` / ``SMTP_*`` environment
variables. That order matters in both directions:

* An operator who sets the transport with env vars (the normal deployment path) needs
  no row at all — nothing is written until someone uses the Email setup screen.
* Once the screen has verified and saved credentials, they must survive a redeploy,
  which is the whole point of not writing ``backend/.env`` any more.

The DB is the source of truth only for mail credentials. Everything else (Slack
webhook, Anthropic key, SECRET_KEY) stays in environment variables.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.email_setting import EmailSetting

logger = logging.getLogger(__name__)

# One sending identity for the whole installation; the row is always id 1.
SINGLETON_ID = 1


def get_row(db: Session) -> Optional[EmailSetting]:
    """Return the stored configuration row, or None when nothing was saved yet."""
    return db.get(EmailSetting, SINGLETON_ID)


def _apply_api(row: EmailSetting) -> None:
    settings.RESEND_API_KEY = row.resend_api_key
    settings.EMAIL_FROM = row.email_from
    settings.ALERT_EMAIL_TO = row.alert_email_to
    # Never leave a stale relay behind: email_service prefers SMTP when both look
    # configured, so a leftover host would send through the wrong transport.
    settings.SMTP_HOST = None
    settings.SMTP_USER = None
    settings.SMTP_PASSWORD = None
    settings.SMTP_FROM = None


def _apply_smtp(row: EmailSetting) -> None:
    settings.SMTP_HOST = row.smtp_host
    settings.SMTP_PORT = row.smtp_port or 587
    settings.SMTP_USER = row.smtp_user
    settings.SMTP_PASSWORD = row.smtp_password
    settings.SMTP_FROM = row.smtp_from
    settings.SMTP_USE_TLS = bool(row.smtp_use_tls)
    settings.SMTP_USE_SSL = bool(row.smtp_use_ssl)
    settings.RESEND_API_KEY = None
    settings.EMAIL_FROM = None
    settings.ALERT_EMAIL_TO = None


def load_into_settings(db: Session) -> bool:
    """Apply the saved row to the in-process settings; True when one was applied.

    Called once at startup. Any failure is the caller's to log — a missing table (an
    old database before ``alembic upgrade head``) must not stop the API from booting,
    it only means alerts fall back to env-var configuration.
    """
    row = get_row(db)
    if row is None:
        return False

    if row.transport == "api" and row.resend_api_key:
        _apply_api(row)
    elif row.transport == "smtp" and row.smtp_host:
        _apply_smtp(row)
    else:
        logger.warning(
            "email_settings: row %s has transport=%r but no usable credentials; "
            "keeping environment-variable configuration",
            row.id,
            row.transport,
        )
        return False

    logger.info(
        "email_settings: applied saved %s configuration from the database", row.transport
    )
    return True


def _row_for_update(db: Session) -> EmailSetting:
    row = get_row(db)
    if row is None:
        row = EmailSetting(id=SINGLETON_ID)
        db.add(row)
    return row


def save_api(
    db: Session,
    *,
    api_key: str,
    from_email: Optional[str] = None,
    alert_email_to: Optional[str] = None,
) -> EmailSetting:
    """Persist a verified API-key transport and apply it to the live settings.

    Call only after the key has been proven with a real test send: this method never
    validates the credential, it stores what the provider already accepted.
    """
    row = _row_for_update(db)
    row.transport = "api"
    row.resend_api_key = api_key
    row.email_from = from_email or None
    row.alert_email_to = alert_email_to or None

    # One active transport at a time — retire any previous SMTP relay.
    row.smtp_host = None
    row.smtp_port = None
    row.smtp_user = None
    row.smtp_password = None
    row.smtp_from = None
    row.smtp_use_tls = True
    row.smtp_use_ssl = False

    db.commit()
    db.refresh(row)
    _apply_api(row)
    logger.info("email_settings: saved API transport")
    return row


def save_smtp(
    db: Session,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    smtp_from: str,
    use_tls: bool,
    use_ssl: bool,
) -> EmailSetting:
    """Persist a verified SMTP relay and make it the active transport."""
    row = _row_for_update(db)
    row.transport = "smtp"
    row.smtp_host = host
    row.smtp_port = port
    row.smtp_user = user or None
    row.smtp_password = password
    row.smtp_from = smtp_from or None
    row.smtp_use_tls = use_tls
    row.smtp_use_ssl = use_ssl

    # One active transport at a time — retire any previous API key.
    row.resend_api_key = None
    row.email_from = None
    row.alert_email_to = None

    db.commit()
    db.refresh(row)
    _apply_smtp(row)
    logger.info("email_settings: saved SMTP transport for host %s", host)
    return row
