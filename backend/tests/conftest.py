"""
backend/tests/conftest.py

Suite-wide test isolation.

The app reads its settings from backend/.env, which on a developer machine (or a
deployment) holds real Slack webhooks and SMTP credentials. Tests must never send a
real alert: Slack is mocked at the httpx boundary, and SMTP is switched off here so a
local .env cannot leak a live mail relay into the suite (which would be slow at best
and would spam real inboxes at worst). A test that wants email delivery enables it
explicitly with its own monkeypatch, after this autouse fixture has run.
"""

from __future__ import annotations

import pytest

from app.core.config import settings


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
