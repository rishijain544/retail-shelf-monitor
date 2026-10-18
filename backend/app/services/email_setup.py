"""
backend/app/services/email_setup.py

Install an email-provider API key at runtime.

Why this exists: alert email needs one credential from a mail provider, and a key with
a typo (or a revoked one) fails as a bare HTTP 401 in the logs — the alert row just says
"Not sent". This module lets the app accept a key from the UI, *prove* it works by
sending a real test message, and only then persist it. A wrong paste is rejected with
the provider's reason instead of silently breaking every future alert.

Verification is a real send rather than a "list domains" call on purpose: Resend keys
can be scoped to sending only, and such a key is perfectly valid even though it is not
allowed to read account resources.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.core import env_file
from app.core.config import settings
from app.services import email_service

logger = logging.getLogger(__name__)


def mask(key: str) -> str:
    """A safe fingerprint of a secret: never enough to reuse, enough to compare."""
    key = email_service.normalize_api_key(key)
    if len(key) <= 10:
        return "***"
    return f"{key[:6]}…{key[-4:]}"


def install(
    api_key: str,
    from_email: Optional[str],
    *,
    test_recipient: str,
    store_name: Optional[str] = None,
    alert_email_to: Optional[str] = None,
) -> dict:
    """Apply, test, then persist new credentials.

    Returns a summary dict on success; raises ValueError with a human explanation when
    the key is malformed or the provider refuses to send. On failure the previous
    settings are restored and ``backend/.env`` is left untouched, so a bad paste can
    never take working alerting down with it.
    """
    key = email_service.normalize_api_key(api_key)
    if not key:
        raise ValueError("an API key is required")
    if not email_service.looks_like_api_key(key):
        raise ValueError(
            "that does not look like a Resend API key — it should start with 're_' "
            "followed by at least 16 characters"
        )

    sender = (from_email or "").strip()
    recipients = (alert_email_to or "").strip()
    previous_key = settings.RESEND_API_KEY
    previous_from = settings.EMAIL_FROM
    previous_to = settings.ALERT_EMAIL_TO
    previous_smtp = {
        "SMTP_HOST": settings.SMTP_HOST,
        "SMTP_PORT": settings.SMTP_PORT,
        "SMTP_USER": settings.SMTP_USER,
        "SMTP_PASSWORD": settings.SMTP_PASSWORD,
        "SMTP_FROM": settings.SMTP_FROM,
        "SMTP_USE_TLS": settings.SMTP_USE_TLS,
        "SMTP_USE_SSL": settings.SMTP_USE_SSL,
    }

    # Apply in memory first so the test message goes through exactly the transport a
    # real alert will use (including the new From: address and recipient override).
    # The API key becomes THE transport: a relay left over from an earlier SMTP setup is
    # cleared, otherwise the verified send below would silently go through that relay
    # and a broken key could still be reported as "verified".
    settings.RESEND_API_KEY = key
    settings.SMTP_HOST = None
    settings.SMTP_USER = None
    settings.SMTP_PASSWORD = None
    settings.SMTP_FROM = None
    if sender:
        settings.EMAIL_FROM = sender
    if recipients:
        settings.ALERT_EMAIL_TO = recipients
    email_service.clear_error()

    delivered = email_service.send_test_message([test_recipient], store_name=store_name)
    if not delivered:
        settings.RESEND_API_KEY = previous_key
        settings.EMAIL_FROM = previous_from
        settings.ALERT_EMAIL_TO = previous_to
        for name, value in previous_smtp.items():
            setattr(settings, name, value)
        raise ValueError(
            email_service.last_error()
            or "the provider accepted the key but the test message was not delivered"
        )

    env_file.upsert("RESEND_API_KEY", key)
    # One active transport at a time: retire the SMTP relay now that the key is proven.
    for name, value in (
        ("SMTP_HOST", ""),
        ("SMTP_USER", ""),
        ("SMTP_PASSWORD", ""),
        ("SMTP_FROM", ""),
    ):
        env_file.upsert(name, value)
    if sender:
        env_file.upsert("EMAIL_FROM", sender)
    if recipients:
        env_file.upsert("ALERT_EMAIL_TO", recipients)
    logger.info(
        "email_setup: installed a new API key (%s) and verified delivery.", mask(key)
    )

    return {
        "configured": email_service.is_configured(),
        "transport": email_service.transport(),
        "from_address": settings.email_from_address,
        "alert_email_to": settings.ALERT_EMAIL_TO,
        "key": mask(key),
        "delivered": delivered,
    }


def install_smtp(
    host: str,
    port: int,
    user: str,
    password: str,
    smtp_from: Optional[str],
    *,
    test_recipient: str,
    store_name: Optional[str] = None,
    use_tls: bool = True,
    use_ssl: bool = False,
) -> dict:
    """Apply, test, then persist SMTP relay credentials.

    Unlike a Resend API key (whose free tier only delivers to the account's own
    address), an SMTP relay — e.g. a Gmail App Password — can deliver to ANY login
    email. That is what this project needs: each signed-in account should get the
    alert at its own address, so SMTP is the transport that makes that work without a
    verified domain or a paid plan.

    The credentials are proven with a real test send to ``test_recipient`` (the
    caller's login email) *before* anything is persisted. On failure the previous
    settings are restored and ``backend/.env`` is left untouched.

    When SMTP is installed it becomes the active transport: the Resend API key and the
    recipient override are cleared (in memory and in ``.env``) so ``email_service``
    routes through SMTP and every login email receives the alert.
    """
    host = (host or "").strip()
    user = (user or "").strip()
    password = (password or "").strip()
    sender = (smtp_from or "").strip() or user

    if not host:
        raise ValueError("an SMTP host is required (e.g. smtp.gmail.com)")
    if not password:
        raise ValueError("an SMTP password (or App Password) is required")
    if use_ssl and use_tls:
        raise ValueError("use either STARTTLS (port 587) or SSL (port 465), not both")

    previous = {
        "SMTP_HOST": settings.SMTP_HOST,
        "SMTP_PORT": settings.SMTP_PORT,
        "SMTP_USER": settings.SMTP_USER,
        "SMTP_PASSWORD": settings.SMTP_PASSWORD,
        "SMTP_FROM": settings.SMTP_FROM,
        "SMTP_USE_TLS": settings.SMTP_USE_TLS,
        "SMTP_USE_SSL": settings.SMTP_USE_SSL,
        "RESEND_API_KEY": settings.RESEND_API_KEY,
        "EMAIL_FROM": settings.EMAIL_FROM,
        "ALERT_EMAIL_TO": settings.ALERT_EMAIL_TO,
    }

    # Apply in memory first. SMTP becomes the active transport: clear the Resend key
    # so _deliver() routes through SMTP (it prefers the API transport when both are
    # set), and clear the recipient override so every login email gets the alert.
    settings.SMTP_HOST = host
    settings.SMTP_PORT = port
    settings.SMTP_USER = user or None
    settings.SMTP_PASSWORD = password
    settings.SMTP_FROM = sender
    settings.SMTP_USE_TLS = use_tls
    settings.SMTP_USE_SSL = use_ssl
    settings.RESEND_API_KEY = None
    settings.EMAIL_FROM = None
    settings.ALERT_EMAIL_TO = None
    email_service.clear_error()

    delivered = email_service.send_test_message([test_recipient], store_name=store_name)
    if not delivered:
        for name, value in previous.items():
            setattr(settings, name, value)
        raise ValueError(
            email_service.last_error()
            or "the SMTP relay accepted the credentials but the test message was not delivered"
        )

    env_file.upsert("SMTP_HOST", host)
    env_file.upsert("SMTP_PORT", str(port))
    env_file.upsert("SMTP_USER", user)
    env_file.upsert("SMTP_PASSWORD", password)
    env_file.upsert("SMTP_FROM", sender)
    env_file.upsert("SMTP_USE_TLS", "true" if use_tls else "false")
    env_file.upsert("SMTP_USE_SSL", "true" if use_ssl else "false")
    # SMTP is now the active transport; clear the Resend key and recipient override so
    # alerts keep going to each login email.
    env_file.upsert("RESEND_API_KEY", "")
    env_file.upsert("EMAIL_FROM", "")
    env_file.upsert("ALERT_EMAIL_TO", "")
    logger.info("email_setup: installed SMTP relay (%s) and verified delivery.", host)

    return {
        "configured": email_service.is_configured(),
        "transport": email_service.transport(),
        "from_address": settings.email_from_address,
        "smtp_host": host,
        "smtp_user": user,
        "delivered": delivered,
    }
