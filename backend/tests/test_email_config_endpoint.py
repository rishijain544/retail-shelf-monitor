"""
backend/tests/test_email_config_endpoint.py

The email-setup flow: a pasted provider key is proven with a real test send, persisted
only when it works, and applied immediately.

The regressions this guards:
* a key the provider rejects used to look "configured" in the dashboard while every
  alert row quietly said "Not sent";
* verified credentials used to be saved to ``backend/.env``, which a deployed container
  cannot write — so after a redeploy every alert silently stopped being emailed.

Persistence is now a row in ``email_settings``; the tests assert the row (not a file)
and simulate a restart to prove the saved transport is reloaded.
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.email_setting import EmailSetting
from app.models.store import Store
from app.models.user import User
from app.services import email_service, email_settings

BASE = "/api/v1"
VALID_KEY = "re_" + "b" * 24


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret-key-with-at-least-32-chars")


@pytest.fixture
def env(tmp_path, monkeypatch):
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
            email="owner@test.com",
            hashed_password="$2b$12$notarealhash",
            store_id=store.id,
        )
        db.add(user)
        db.commit()
        user_id = user.id
    finally:
        db.close()

    email_service.clear_error()
    yield {
        "client": TestClient(app),
        "token": create_access_token(user_id),
        "SessionLocal": TestingSessionLocal,
    }
    app.dependency_overrides.clear()
    email_service.clear_error()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _saved(email_env) -> EmailSetting | None:
    """Read the singleton row from a fresh session, as the next process would."""
    db = email_env["SessionLocal"]()
    try:
        return db.get(EmailSetting, email_settings.SINGLETON_ID)
    finally:
        db.close()


def _provider(status_code: int = 202, sent: list | None = None):
    def _fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        if sent is not None:
            sent.append({"url": url, "headers": headers, "json": json})
        body = '{"id":"abc"}' if status_code < 300 else '{"message":"API key is invalid"}'
        return types.SimpleNamespace(status_code=status_code, text=body)

    return _fake_post


def test_rejects_a_malformed_key_without_saving_it(env, monkeypatch):
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(202))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": "not-a-resend-key"},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 400, resp.text
    assert "re_" in resp.json()["detail"]
    assert _saved(env) is None
    assert not settings.RESEND_API_KEY


def test_rejects_a_key_the_provider_does_not_recognise(env, monkeypatch):
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(401))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": VALID_KEY},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 400, resp.text
    assert "rejected the API key" in resp.json()["detail"]
    assert _saved(env) is None
    # Nothing half-applied: the in-memory settings were rolled back too.
    assert not settings.RESEND_API_KEY


def test_a_rejected_key_keeps_the_previous_working_configuration(env, monkeypatch):
    db = env["SessionLocal"]()
    try:
        email_settings.save_api(db, api_key="re_" + "o" * 24)
    finally:
        db.close()
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(401))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": VALID_KEY},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 400, resp.text
    assert settings.RESEND_API_KEY == "re_" + "o" * 24
    saved = _saved(env)
    assert saved is not None and saved.resend_api_key == "re_" + "o" * 24


def test_saves_verifies_and_test_sends(env, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(202, sent))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": VALID_KEY, "from_email": "ShelfMonitor <alerts@test.dev>"},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["transport"] == "api"
    assert body["email_delivered_to"] == ["owner@test.com"]
    assert body["key"] != VALID_KEY  # masked fingerprint only

    saved = _saved(env)
    assert saved is not None
    assert saved.transport == "api"
    assert saved.resend_api_key == VALID_KEY
    assert saved.email_from == "ShelfMonitor <alerts@test.dev>"
    assert settings.RESEND_API_KEY == VALID_KEY
    assert settings.EMAIL_FROM == "ShelfMonitor <alerts@test.dev>"

    assert sent and sent[0]["json"]["to"] == ["owner@test.com"]
    assert sent[0]["json"]["from"] == "ShelfMonitor <alerts@test.dev>"
    assert sent[0]["headers"]["Authorization"] == f"Bearer {VALID_KEY}"

    health = env["client"].get(f"{BASE}/health").json()
    assert health["services"]["alert_email"] == "healthy"


def test_quotes_and_whitespace_around_a_pasted_key_are_tolerated(env, monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(202, sent))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": f'  "{VALID_KEY}"\r\n'},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 200, resp.text
    assert sent[0]["headers"]["Authorization"] == f"Bearer {VALID_KEY}"
    saved = _saved(env)
    assert saved is not None and saved.resend_api_key == VALID_KEY


def test_requires_authentication(env):
    resp = env["client"].post(f"{BASE}/alerts/email-config", json={"api_key": VALID_KEY})
    assert resp.status_code in (401, 403)


class _FakeSMTP:
    """Stands in for smtplib so the SMTP path is exercised without a socket."""

    instances: list["_FakeSMTP"] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host = host
        self.port = port
        self.logged_in: tuple[str, str] | None = None
        self.sent: list[tuple] = []
        _FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        self.logged_in = (user, password)

    def send_message(self, message, from_addr=None, to_addrs=None):
        self.sent.append((message, from_addr, to_addrs))


def test_smtp_relay_is_saved_once_and_survives_a_restart(env, monkeypatch):
    """The complaint this guards: entering the mail credentials again and again.

    The relay is a *sending* credential, so one save must serve every account and be
    reported as configured on the next page load — including after a redeploy, which is
    why it has to land in the database rather than in ``backend/.env``. Alerts
    themselves are addressed from the ``users`` table, so each person receives them at
    the address they log in with.
    """
    _FakeSMTP.instances = []
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP_SSL", _FakeSMTP)

    resp = env["client"].post(
        f"{BASE}/alerts/smtp-config",
        json={
            "host": "smtp.example.test",
            "port": 587,
            "user": "relay@example.test",
            "password": "app-password",
        },
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["configured"] is True
    assert body["transport"] == "smtp"
    assert body["smtp_host"] == "smtp.example.test"
    assert body["smtp_user"] == "relay@example.test"
    # The verification message went to the account's own login address.
    assert body["email_delivered_to"] == ["owner@test.com"]

    (server,) = _FakeSMTP.instances
    assert server.logged_in == ("relay@example.test", "app-password")
    (message, _from, to_addrs) = server.sent[0]
    assert to_addrs == ["owner@test.com"]
    assert message["To"] == "owner@test.com"

    # Stored, so a restart keeps it.
    saved = _saved(env)
    assert saved is not None
    assert saved.transport == "smtp"
    assert saved.smtp_host == "smtp.example.test"
    assert saved.smtp_user == "relay@example.test"
    assert saved.smtp_password == "app-password"
    assert saved.smtp_use_tls is True
    assert saved.smtp_use_ssl is False

    # Simulate the next process: env vars blank, then the startup load.
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)
    monkeypatch.setattr(settings, "SMTP_FROM", None)
    monkeypatch.setattr(settings, "RESEND_API_KEY", None)
    db = env["SessionLocal"]()
    try:
        assert email_settings.load_into_settings(db) is True
    finally:
        db.close()
    assert settings.SMTP_HOST == "smtp.example.test"
    assert settings.SMTP_USER == "relay@example.test"
    assert settings.SMTP_PASSWORD == "app-password"
    assert settings.smtp_from_address == "relay@example.test"

    # Reloading the page must say "already configured" — no second prompt.
    again = env["client"].get(
        f"{BASE}/alerts/email-config", headers=_auth(env["token"])
    ).json()
    assert again["configured"] is True
    assert again["transport"] == "smtp"
    assert again["last_error"] is None

    health = env["client"].get(f"{BASE}/health").json()
    assert health["services"]["alert_email"] == "healthy"
    assert health["services"]["alert_email_transport"] == "smtp"


def test_installing_an_api_key_retires_the_stored_smtp_relay(env, monkeypatch):
    """One active transport at a time: a stale relay must not shadow the new key."""
    db = env["SessionLocal"]()
    try:
        email_settings.save_smtp(
            db,
            host="smtp.example.test",
            port=587,
            user="relay@example.test",
            password="app-password",
            smtp_from="relay@example.test",
            use_tls=True,
            use_ssl=False,
        )
    finally:
        db.close()
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(202))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": VALID_KEY},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["transport"] == "api"
    saved = _saved(env)
    assert saved is not None
    assert saved.transport == "api"
    assert saved.smtp_host is None
    assert saved.smtp_password is None
    assert settings.SMTP_HOST is None
