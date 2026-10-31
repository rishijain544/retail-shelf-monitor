"""
backend/tests/conftest.py

Suite-wide test isolation.

The app reads its settings from backend/.env, which on a developer machine (or a
deployment) holds real Slack webhooks and SMTP credentials. Tests must never send a
real alert: Slack is mocked at the httpx boundary, and SMTP is switched off here so a
local .env cannot leak a live mail relay into the suite (which would be slow at best
and would spam real inboxes at worst). A test that wants email delivery enables it
explicitly with its own monkeypatch, after this autouse fixture has run.

Startup migrations are switched off here for the same reason: the app applies the
Alembic chain on boot, but every test builds and owns its own throwaway schema (and
the engine at import time points at a developer's real database). A test that wants
the boot-time upgrade enables it explicitly.
"""

from __future__ import annotations

import pytest

from app.core.config import settings
from app.services import email_service

# A valid Fernet key (base64 of 32 zero bytes). Pinned for every test so an enrolled
# secret encrypted in one test module can be read in another, and so a developer's
# backend/.env cannot change the key mid-suite.
TEST_MFA_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture(autouse=True)
def _disable_real_email_delivery(monkeypatch):
    """Blank the SMTP settings for every test; opt back in per test when needed."""
    monkeypatch.setattr(settings, "RESEND_API_KEY", None)
    monkeypatch.setattr(settings, "EMAIL_FROM", None)
    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", None)
    monkeypatch.setattr(settings, "EMAIL_API_URL", "https://api.resend.com/emails")
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)
    monkeypatch.setattr(settings, "SMTP_FROM", None)
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(settings, "SMTP_USE_SSL", False)
    monkeypatch.setattr(settings, "ALERT_EMAIL_ENABLED", True)


@pytest.fixture(autouse=True)
def _disable_startup_migrations(monkeypatch):
    """Never let a test's app startup migrate a real database."""
    monkeypatch.setattr(settings, "AUTO_MIGRATE", False)


@pytest.fixture(autouse=True)
def _pin_mfa_encryption_key(monkeypatch):
    """Keep TOTP encryption deterministic and independent of the local .env."""
    monkeypatch.setattr(settings, "MFA_ENCRYPTION_KEY", TEST_MFA_KEY)


@pytest.fixture
def verification_codes(monkeypatch):
    """Capture signup verification emails instead of sending them.

    Returns the list of captured messages; each entry has the address and the
    plaintext code, since a test has no mailbox to read. Delivery itself is covered
    by test_email_service, which mocks at the SMTP/HTTP boundary.
    """
    sent: list[dict] = []

    def fake_send(to_email, code, *, store_name=None):
        sent.append({"to": to_email, "code": code, "store_name": store_name})
        return [to_email]

    monkeypatch.setattr(email_service, "send_verification_code", fake_send)
    return sent
