"""
backend/tests/test_slack_service.py

Unit tests for the real Slack integration in app.services.slack_service, plus the
POST /alerts/test endpoint. The network boundary (httpx.post) is always mocked, so no
test ever hits a real webhook.
"""
from __future__ import annotations

import types

import httpx
import pytest
from fastapi.testclient import TestClient

import types

import httpx
import pytest
from fastapi.testclient import TestClient

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.main import app
from app.models.store import Store
from app.models.user import User
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.services import slack_service

WEBHOOK = "https://hooks.slack.com/services/T00/B00/FAKE"

TEST_SECRET = "test-secret-key-with-at-least-32-chars"


@pytest.fixture
def slack_capture(monkeypatch):
    """Configure a fake webhook and capture httpx.post calls.

    Defaults to a successful Slack reply (HTTP 200, body "ok"). A test can flip
    `state` to simulate a rejection (non-200 / non-"ok") or a transport error.
    """
    calls: list[dict] = []
    state = {"status_code": 200, "text": "ok", "raise": None}

    def fake_post(url, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "timeout": timeout})
        if state["raise"] is not None:
            raise state["raise"]
        return types.SimpleNamespace(status_code=state["status_code"], text=state["text"])

    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setattr("app.services.slack_service.httpx.post", fake_post)
    return {"calls": calls, "state": state}


@pytest.fixture
def client():
    return TestClient(app)


# ─── notify_high_urgency ─────────────────────────────────────────────────────

def test_notify_high_urgency_posts_readable_message(slack_capture):
    ok = slack_service.notify_high_urgency(
        aisle="Aisle 7",
        fill_percentage=0.18,
        summary="Shelf is nearly empty; several gaps detected.",
        recommended_action="Restock cereal within the hour.",
    )
    assert ok is True

    assert len(slack_capture["calls"]) == 1
    call = slack_capture["calls"][0]
    assert call["url"] == WEBHOOK

    text = call["json"]["text"]
    # Human-readable, not a raw JSON dump of the report.
    assert "Aisle 7" in text                              # aisle name
    assert "18" in text                                   # 18.0% fill
    assert "Shelf is nearly empty" in text                # LLM summary
    assert "Restock cereal within the hour." in text      # recommended action
    assert "{" not in text and "}" not in text            # not a JSON blob


def test_notify_high_urgency_no_webhook_skips_post(monkeypatch, caplog):
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    user_id = _seed_user(monkeypatch)
    called = False

    def fake_post(*args, **kwargs):
        nonlocal called
        called = True
        return types.SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr("app.services.slack_service.httpx.post", fake_post)

    with caplog.at_level("WARNING"):
        ok = slack_service.notify_high_urgency(
            aisle="Aisle 7", fill_percentage=0.1, summary="x", recommended_action="y",
        )

    assert ok is False   # reported as not-sent, rather than crashing the pipeline
    assert called is False  # never touched the network
    assert any("SLACK_WEBHOOK_URL" in r.getMessage() for r in caplog.records)


def test_notify_high_urgency_non_ok_response_returns_false(slack_capture):
    slack_capture["state"]["status_code"] = 500
    slack_capture["state"]["text"] = "invalid_payload"

    ok = slack_service.notify_high_urgency(
        aisle="A", fill_percentage=0.1, summary="s", recommended_action="a",
    )
    assert ok is False
    assert len(slack_capture["calls"]) == 1  # attempted exactly once


def test_notify_high_urgency_200_but_not_ok_body_returns_false(slack_capture):
    # Slack returns 200 with an error body in some failure modes.
    slack_capture["state"]["status_code"] = 200
    slack_capture["state"]["text"] = "no_service"
    ok = slack_service.notify_high_urgency(
        aisle="A", fill_percentage=0.1, summary="s", recommended_action="a",
    )
    assert ok is False


def test_post_swallows_transport_error(slack_capture):
    slack_capture["state"]["raise"] = httpx.ConnectError("boom")
    # Must not propagate — a Slack outage should never crash the pipeline.
    ok = slack_service.send_test_message()
    assert ok is False


def test_send_test_message_posts(slack_capture):
    ok = slack_service.send_test_message()
    assert ok is True
    assert len(slack_capture["calls"]) == 1
    assert slack_capture["calls"][0]["json"]["text"]  # non-empty message


# ─── shared helpers ──────────────────────────────────────────────────────────

def _seed_user(monkeypatch):
    """Create an in-memory user + store so /alerts/test auth can be satisfied."""
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

    db = TestingSessionLocal()
    try:
        store = Store(name="Test Store", location="Testville")
        db.add(store)
        db.flush()
        user = User(
            email="user@test.com",
            hashed_password="$2b$12$usestexamplehashnotreal",
            store_id=store.id,
        )
        db.add(user)
        db.commit()
        user_id = user.id
    finally:
        db.close()

    return user_id


# ─── POST /alerts/test endpoint ──────────────────────────────────────────────

def test_alerts_test_endpoint_sends(slack_capture, client, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    user_id = _seed_user(monkeypatch)
    resp = client.post(
        "/api/v1/alerts/test",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "sent"
    assert len(slack_capture["calls"]) == 1


def test_alerts_test_endpoint_503_when_unconfigured(monkeypatch, client):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    user_id = _seed_user(monkeypatch)
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)
    resp = client.post(
        "/api/v1/alerts/test",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )
    assert resp.status_code == 503


def test_alerts_test_endpoint_502_when_send_fails(slack_capture, client, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    user_id = _seed_user(monkeypatch)
    slack_capture["state"]["status_code"] = 500
    slack_capture["state"]["text"] = "error"
    resp = client.post(
        "/api/v1/alerts/test",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )
    assert resp.status_code == 502
