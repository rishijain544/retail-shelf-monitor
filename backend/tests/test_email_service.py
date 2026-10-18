"""
backend/tests/test_email_service.py

Unit tests for app.services.email_service and POST /alerts/test's email channel.

The SMTP boundary (smtplib.SMTP / smtplib.SMTP_SSL) is always replaced by a fake, so no
test opens a socket or delivers a real message.
"""

from __future__ import annotations

import types

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.store import Store
from app.models.user import User
from app.services import email_service

TEST_SECRET = "test-secret-key-with-at-least-32-chars"

# A key shaped like a real one ("re_" + 20+ chars): email_service rejects a key of the
# wrong shape as a configuration error rather than making a doomed HTTP call.
API_KEY = "re_" + "t" * 24


# ─── fake SMTP relay ──────────────────────────────────────────────────────────

class FakeSMTP:
    """Records what the service would have handed to the relay."""

    instances: list["FakeSMTP"] = []
    state: dict = {}

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.started_tls = False
        self.logged_in: tuple[str, str] | None = None
        self.sent: list[tuple] = []
        FakeSMTP.instances.append(self)

    # used as a context manager in email_service._deliver
    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context=None):
        self.started_tls = True

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message, from_addr=None, to_addrs=None):
        if FakeSMTP.state.get("raise") is not None:
            raise FakeSMTP.state["raise"]
        self.sent.append((message, from_addr, to_addrs))


@pytest.fixture
def relay(monkeypatch):
    """Configure SMTP + capture deliveries through FakeSMTP."""
    FakeSMTP.instances = []
    FakeSMTP.state = {"raise": None}

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USER", "alerts@example.test")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "SMTP_FROM", "ShelfMonitor <alerts@example.test>")
    monkeypatch.setattr(settings, "SMTP_USE_TLS", True)
    monkeypatch.setattr(settings, "SMTP_USE_SSL", False)

    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", FakeSMTP)
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP_SSL", FakeSMTP)
    return FakeSMTP


def _sent_messages(relay) -> list[tuple]:
    return [msg for server in relay.instances for msg in server.sent]


# ─── notify_high_urgency ──────────────────────────────────────────────────────

def test_notify_high_urgency_emails_a_readable_alert(relay):
    delivered = email_service.notify_high_urgency(
        to_emails=["owner@shop.test"],
        store_name="Downtown Flagship Store",
        aisle="Aisle 1 - Beverages",
        fill_percentage=0.024,
        summary="Aisle 1 - Beverages is critically low; 1 empty region detected.",
        recommended_action="Restock beverages before the next capture.",
    )

    assert delivered == ["owner@shop.test"]
    messages = _sent_messages(relay)
    assert len(messages) == 1

    message, from_addr, to_addrs = messages[0]
    assert to_addrs == ["owner@shop.test"]
    assert from_addr == settings.smtp_from_address

    subject = message["Subject"]
    assert "[ShelfMonitor]" in subject
    assert "High urgency" in subject
    assert "Aisle 1 - Beverages" in subject
    assert "2.4% fill" in subject            # 0.024 -> 2.4%, not 0.0% or 2%

    body = message.get_body(preferencelist=("plain",)).get_content()
    assert "Downtown Flagship Store" in body
    assert "Aisle 1 - Beverages" in body
    assert "2.4%" in body
    assert "critically low" in body
    assert "Restock beverages before the next capture." in body
    # Human-readable prose, not a JSON dump of the report.
    assert "{" not in body and "}" not in body

    # A styled HTML alternative rides along for mail clients that prefer it.
    alternatives = message.get_payload()
    assert any(part.get_content_type() == "text/html" for part in alternatives)


def test_email_is_sent_to_every_recipient_once(relay):
    delivered = email_service.notify_high_urgency(
        to_emails=[
            "owner@shop.test",
            " OWNER@Shop.Test ",   # same address, different case/spacing
            "",
            None,
            "staff@shop.test",
        ],
        aisle="Aisle 4",
        fill_percentage=0.1,
        summary="Shelf is nearly empty.",
        recommended_action="Restock now.",
    )

    assert delivered == ["owner@shop.test", "staff@shop.test"]  # deduped, empties dropped
    assert len(_sent_messages(relay)) == 2


def test_no_recipients_is_a_no_op(relay):
    assert email_service.notify_high_urgency(
        to_emails=[],
        aisle="Aisle 4",
        fill_percentage=0.1,
        summary="s",
        recommended_action="a",
    ) == []
    assert relay.instances == []  # never opened a connection


def test_unconfigured_smtp_skips_delivery(relay, monkeypatch, caplog):
    monkeypatch.setattr(settings, "SMTP_HOST", None)

    with caplog.at_level("WARNING"):
        delivered = email_service.notify_high_urgency(
            to_emails=["owner@shop.test"],
            aisle="Aisle 4",
            fill_percentage=0.1,
            summary="s",
            recommended_action="a",
        )

    assert delivered == []
    assert relay.instances == []
    assert any("no email transport configured" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize(
    "exc",
    [
        ConnectionRefusedError("relay down"),
        TimeoutError("timed out"),
        email_service.smtplib.SMTPAuthenticationError(535, b"bad credentials"),
    ],
)
def test_delivery_errors_never_propagate(relay, exc):
    relay.state["raise"] = exc

    delivered = email_service.notify_high_urgency(
        to_emails=["owner@shop.test"],
        aisle="Aisle 4",
        fill_percentage=0.1,
        summary="s",
        recommended_action="a",
    )

    assert delivered == []  # reported as not-sent, the pipeline still completes


def test_starttls_and_login_are_used_for_port_587(relay):
    email_service.send_test_message(["owner@shop.test"])

    (server,) = relay.instances
    assert (server.host, server.port) == ("smtp.example.test", 587)
    assert server.started_tls is True
    assert server.logged_in == ("alerts@example.test", "app-password")


def test_implicit_tls_skips_starttls(relay, monkeypatch):
    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    monkeypatch.setattr(settings, "SMTP_USE_TLS", False)
    monkeypatch.setattr(settings, "SMTP_USE_SSL", True)

    email_service.send_test_message(["owner@shop.test"])

    (server,) = relay.instances
    assert server.port == 465
    assert server.started_tls is False


def test_disabled_switch_blocks_sending(relay, monkeypatch):
    monkeypatch.setattr(settings, "ALERT_EMAIL_ENABLED", False)

    assert email_service.notify_high_urgency(
        to_emails=["owner@shop.test"],
        aisle="Aisle 4",
        fill_percentage=0.1,
        summary="s",
        recommended_action="a",
    ) == []
    assert relay.instances == []


# ─── email API transport (no SMTP username/password) ─────────────────────────

@pytest.fixture
def api(monkeypatch):
    """Configure the email API and capture the HTTPS calls it makes."""
    calls: list[dict] = []
    state = {"status": 200, "text": '{"id": "msg_1"}', "raise": None}

    def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "headers": headers, "json": json})
        if state["raise"] is not None:
            raise state["raise"]
        return types.SimpleNamespace(status_code=state["status"], text=state["text"])

    monkeypatch.setattr(settings, "RESEND_API_KEY", API_KEY)
    monkeypatch.setattr(settings, "EMAIL_FROM", None)
    monkeypatch.setattr("app.services.email_service.httpx.post", fake_post)
    return {"calls": calls, "state": state}


def test_api_key_alone_is_enough_no_smtp_credentials(api):
    """An API key must deliver mail with SMTP_USER/SMTP_PASSWORD/SMTP_HOST all unset."""
    assert settings.SMTP_HOST is None
    assert settings.SMTP_USER is None
    assert settings.SMTP_PASSWORD is None
    assert email_service.transport() == "api"

    delivered = email_service.notify_high_urgency(
        to_emails=["owner@shop.test"],
        store_name="Downtown Flagship Store",
        aisle="Aisle 1 - Beverages",
        fill_percentage=0.024,
        summary="Aisle 1 - Beverages is critically low.",
        recommended_action="Restock beverages.",
    )

    assert delivered == ["owner@shop.test"]
    assert len(api["calls"]) == 1

    call = api["calls"][0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == f"Bearer {API_KEY}"

    payload = call["json"]
    assert payload["from"] == "ShelfMonitor <onboarding@resend.dev>"
    assert payload["to"] == ["owner@shop.test"]
    assert "High urgency" in payload["subject"]
    assert "2.4% fill" in payload["subject"]
    assert "critically low" in payload["text"]
    assert "<html>" in payload["html"]


def test_smtp_transport_wins_over_the_api(relay, api):
    """With both configured, SMTP is used: it can reach any login address.

    A Resend free-tier key still only delivers to the Resend account owner, so
    preferring it here would silently starve every other account's alert email.
    """
    assert email_service.transport() == "smtp"

    delivered = email_service.send_test_message(["owner@shop.test"])

    assert delivered == ["owner@shop.test"]
    assert api["calls"] == []  # the API key was not needed
    assert len(_sent_messages(relay)) == 1


def test_custom_email_from_is_used(api, monkeypatch):
    monkeypatch.setattr(settings, "EMAIL_FROM", "ShelfMonitor <alerts@shop.test>")

    email_service.send_test_message(["owner@shop.test"])

    assert api["calls"][0]["json"]["from"] == "ShelfMonitor <alerts@shop.test>"


def test_api_rejection_reports_not_sent(api, caplog):
    api["state"]["status"] = 403
    api["state"]["text"] = '{"message": "domain is not verified"}'

    with caplog.at_level("ERROR"):
        delivered = email_service.notify_high_urgency(
            to_emails=["owner@shop.test"],
            aisle="Aisle 4",
            fill_percentage=0.1,
            summary="s",
            recommended_action="a",
        )

    assert delivered == []
    assert any("domain is not verified" in r.getMessage() for r in caplog.records)


def test_api_network_error_never_propagates(api):
    api["state"]["raise"] = httpx.ConnectError("boom")

    assert email_service.notify_high_urgency(
        to_emails=["owner@shop.test"],
        aisle="Aisle 4",
        fill_percentage=0.1,
        summary="s",
        recommended_action="a",
    ) == []


def test_no_transport_configured_reports_not_sent():
    assert email_service.is_configured() is False
    assert email_service.transport() == "none"


def test_send_test_message_is_explicit_it_is_a_drill(relay):
    delivered = email_service.send_test_message(["owner@shop.test"], store_name="Downtown")

    assert delivered == ["owner@shop.test"]
    (message, _from, _to) = _sent_messages(relay)[0]
    assert "Test alert" in message["Subject"]
    assert "Downtown" in message.get_body(preferencelist=("plain",)).get_content()


# ─── POST /alerts/test ────────────────────────────────────────────────────────

def _seed_user() -> int:
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
        return user.id
    finally:
        db.close()


def _slack_ok(monkeypatch, *, ok: bool = True) -> list[dict]:
    calls: list[dict] = []

    def fake_post(url, json=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json})
        return types.SimpleNamespace(status_code=200 if ok else 500, text="ok" if ok else "err")

    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X")
    monkeypatch.setattr("app.services.slack_service.httpx.post", fake_post)
    return calls


def test_test_alert_delivers_to_slack_and_the_callers_login_email(relay, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    slack_calls = _slack_ok(monkeypatch)
    user_id = _seed_user()

    resp = TestClient(app).post(
        "/api/v1/alerts/test",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "sent"
    assert body["slack"] == "sent"
    assert body["email"] == "sent"
    assert body["email_recipients"] == ["user@test.com"]
    assert "user@test.com" in body["detail"]

    # Both channels really fired: one webhook post, one SMTP message to the caller.
    assert len(slack_calls) == 1
    (message, _from, to_addrs) = _sent_messages(relay)[0]
    assert to_addrs == ["user@test.com"]
    assert message["To"] == "user@test.com"


def test_test_alert_sends_via_api_key_without_smtp(api, monkeypatch):
    """The dashboard's test button must deliver with only an API key configured."""
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)
    user_id = _seed_user()

    resp = TestClient(app).post(
        "/api/v1/alerts/test",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["slack"] == "not_configured"
    assert body["email"] == "sent"
    assert body["email_recipients"] == ["user@test.com"]
    assert api["calls"][0]["json"]["to"] == ["user@test.com"]


def test_test_alert_succeeds_on_email_when_slack_is_unconfigured(relay, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)
    user_id = _seed_user()

    resp = TestClient(app).post(
        "/api/v1/alerts/test",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["slack"] == "not_configured"
    assert resp.json()["email"] == "sent"


def test_test_alert_502_when_every_configured_channel_fails(relay, monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)
    _slack_ok(monkeypatch, ok=False)
    relay.state["raise"] = ConnectionRefusedError("relay down")
    user_id = _seed_user()

    resp = TestClient(app).post(
        "/api/v1/alerts/test",
        headers={"Authorization": f"Bearer {create_access_token(user_id)}"},
    )

    assert resp.status_code == 502
    assert "slack=failed" in resp.json()["detail"]
    assert "email=failed" in resp.json()["detail"]

