"""
backend/tests/test_email_config_endpoint.py

The email-setup flow: a pasted provider key is proven with a real test send, persisted to
.env only when it works, and applied immediately.

The regression this guards: a key the provider rejects used to look "configured" in the
dashboard while every alert row quietly said "Not sent".
"""

from __future__ import annotations

import types

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core import env_file
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.store import Store
from app.models.user import User
from app.services import email_service

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

    # Never touch the developer's real backend/.env from a test.
    env_path = tmp_path / ".env"
    env_path.write_text("PROJECT_NAME=\"Test\"\nRESEND_API_KEY=\n", encoding="utf-8")
    monkeypatch.setattr(env_file, "ENV_PATH", str(env_path))

    email_service.clear_error()
    yield {
        "client": TestClient(app),
        "token": create_access_token(user_id),
        "env_path": str(env_path),
    }
    app.dependency_overrides.clear()
    email_service.clear_error()


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _provider(status_code: int = 202, sent: list | None = None):
    def _fake_post(url, headers=None, json=None, timeout=None, **kwargs):
        if sent is not None:
            sent.append({"url": url, "headers": headers, "json": json})
        body = '{"id":"abc"}' if status_code < 300 else '{"message":"API key is invalid"}'
        return types.SimpleNamespace(status_code=status_code, text=body)

    return _fake_post


def test_rejects_a_malformed_key_without_touching_env(env, monkeypatch):
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(202))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": "not-a-resend-key"},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 400, resp.text
    assert "re_" in resp.json()["detail"]
    assert "RESEND_API_KEY=\n" in open(env["env_path"], encoding="utf-8").read()
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
    assert VALID_KEY not in open(env["env_path"], encoding="utf-8").read()
    # Nothing half-applied: the in-memory settings were rolled back too.
    assert not settings.RESEND_API_KEY


def test_a_rejected_key_keeps_the_previous_working_configuration(env, monkeypatch):
    monkeypatch.setattr(settings, "RESEND_API_KEY", "re_" + "o" * 24)
    monkeypatch.setattr("app.services.email_service.httpx.post", _provider(401))

    resp = env["client"].post(
        f"{BASE}/alerts/email-config",
        json={"api_key": VALID_KEY},
        headers=_auth(env["token"]),
    )

    assert resp.status_code == 400, resp.text
    assert settings.RESEND_API_KEY == "re_" + "o" * 24
    assert "RESEND_API_KEY=\n" in open(env["env_path"], encoding="utf-8").read()


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

    text = open(env["env_path"], encoding="utf-8").read()
    assert f"RESEND_API_KEY={VALID_KEY}" in text
    assert "PROJECT_NAME=\"Test\"" in text  # untouched
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


def test_smtp_relay_is_saved_once_and_then_never_asked_for_again(env, monkeypatch):
    """The complaint this guards: entering the mail credentials again and again.

    The relay is a *sending* credential, so one save must serve every account and be
    reported as configured on the next page load — including after a restart, which is
    why it has to land in ``backend/.env`` rather than only in memory. Alerts themselves
    are addressed from the ``users`` table, so each person receives them at the address
    they log in with.
    """
    _FakeSMTP.instances = []
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", _FakeSMTP)
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP_SSL", _FakeSMTP)
    # This test rewrites settings directly (that is what install_smtp does), so put the
    # module-level singleton back afterwards instead of leaking an SMTP relay into
    # every later test in this file.
    restore = {
        name: getattr(settings, name)
        for name in (
            "SMTP_HOST",
            "SMTP_PORT",
            "SMTP_USER",
            "SMTP_PASSWORD",
            "SMTP_FROM",
            "SMTP_USE_TLS",
            "SMTP_USE_SSL",
            "RESEND_API_KEY",
            "EMAIL_FROM",
            "ALERT_EMAIL_TO",
        )
    }
    monkeypatch.setattr(settings, "SMTP_HOST", None)
    monkeypatch.setattr(settings, "SMTP_USER", None)
    monkeypatch.setattr(settings, "SMTP_PASSWORD", None)
    monkeypatch.setattr(settings, "SMTP_FROM", None)
    monkeypatch.setattr(settings, "RESEND_API_KEY", None)
    monkeypatch.setattr(settings, "ALERT_EMAIL_TO", None)

    try:
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

        # Persisted, so a restart (a fresh Settings read of .env) keeps it.
        text = open(env["env_path"], encoding="utf-8").read()
        assert "SMTP_HOST=smtp.example.test" in text
        assert "SMTP_USER=relay@example.test" in text
        assert "SMTP_PASSWORD=app-password" in text

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
    finally:
        for name, value in restore.items():
            setattr(settings, name, value)


def test_upsert_preserves_the_rest_of_the_file(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\r\nOTHER=1\r\nRESEND_API_KEY=old\r\n", encoding="utf-8")

    env_file.upsert("RESEND_API_KEY", "re_new", path=str(path))

    text = path.read_text(encoding="utf-8")
    assert "RESEND_API_KEY=re_new" in text
    assert "RESEND_API_KEY=old" not in text
    assert "OTHER=1" in text and "# comment" in text
