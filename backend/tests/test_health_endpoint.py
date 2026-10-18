"""
backend/tests/test_health_endpoint.py

Tests for GET /health.

The endpoint reports API + database liveness and must always answer promptly,
degrading to ``unhealthy: <reason>`` strings instead of blocking or failing.
(Redis was removed from the stack; the historical regression tests that guarded
against a hanging Redis probe are gone with it.)
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.database import Base, get_db
from app.main import app

BASE = "/api/v1"


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_health_reports_healthy_api_and_database(client):
    resp = client.get(f"{BASE}/health")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["services"]["api"] == "healthy"
    assert body["services"]["database"] == "healthy"


def test_health_reports_alert_channel_configuration(client, monkeypatch):
    """The dashboard must be able to say *why* no alert email arrives.

    The suite blanks every delivery setting (see tests/conftest.py); the Slack webhook is
    cleared here too so the test does not depend on a developer's real .env. Both
    channels then report not_configured and the email transport is "none" — the state
    that silently produced "Not sent" rows before it was surfaced here.
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)

    body = client.get(f"{BASE}/health").json()

    assert body["services"]["alert_slack"] == "not_configured"
    assert body["services"]["alert_email"] == "not_configured"
    assert body["services"]["alert_email_transport"] == "none"


def test_health_reports_configured_email_transport(client, monkeypatch):
    from app.core.config import settings
    from app.services import email_service

    # A realistically shaped key: the health check rejects obvious non-keys, so a short
    # placeholder would (correctly) report a configuration problem instead.
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_" + "a" * 24)
    email_service.clear_error()

    body = client.get(f"{BASE}/health").json()

    assert body["services"]["alert_email"] == "healthy"
    assert body["services"]["alert_email_transport"] == "api"


def test_health_surfaces_a_rejected_email_key(client, monkeypatch):
    """A configured-but-rejected key must not be reported as healthy."""
    from app.core.config import settings

    from app.services import email_service

    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_" + "a" * 24)
    monkeypatch.setattr(
        email_service, "last_error", lambda: "the email provider rejected the API key (HTTP 401)"
    )

    body = client.get(f"{BASE}/health").json()

    assert body["services"]["alert_email"].startswith("unhealthy:")
    assert "rejected the API key" in body["services"]["alert_email"]


def test_health_reports_unhealthy_database(client, monkeypatch):
    # Point get_db at a session bound to a broken engine and make the query fail.
    class _BrokenDB:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("Connection refused")

    def broken_get_db():
        yield _BrokenDB()

    app.dependency_overrides[get_db] = broken_get_db
    try:
        resp = client.get(f"{BASE}/health")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "unhealthy"
        assert body["services"]["database"].startswith("unhealthy:")
        assert "Connection refused" in body["services"]["database"]
    finally:
        app.dependency_overrides.clear()
