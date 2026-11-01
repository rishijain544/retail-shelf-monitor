"""
backend/tests/test_password_reset.py

The forgot-password flow, end to end, with no mailbox and no real SMTP:

* an unknown address is indistinguishable from a known one — same 202, same body, no
  email, and the lookup/mail happen after the response;
* the shared demo account and every ``.test`` address never get a token;
* the token is single-use, expires, is superseded by a newer request, and only its
  SHA-256 digest is ever stored;
* a successful reset does not sign anyone in (no token of any kind in the response) and
  does not bypass MFA — an enrolled account still has to present a code;
* every JWT issued before the reset is refused afterwards, including an
  ``mfa_challenge`` token;
* a reset clears a failed-login lockout but leaves MFA state and its lockout alone.

The background task opens its own session through ``password_reset.SessionLocal``; the
``session_factory`` fixture points that at the same in-memory database as the app.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.clock import as_utc, utcnow
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.password_reset_token import PasswordResetToken
from app.models.store import Store
from app.models.user import User
from app.services import email_service, mfa, password_reset

BASE = "/api/v1"
TEST_SECRET = "test-secret-key-with-at-least-32-chars"

PASSWORD = "correct-horse"
NEW_PASSWORD = "brand-new-horse"
EMAIL = "owner@shop.com"


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)


@pytest.fixture(autouse=True)
def _reset_rate_limits():
    """Every test starts with empty per-IP budgets."""
    password_reset.reset_rate_limits()
    yield
    password_reset.reset_rate_limits()


@pytest.fixture(autouse=True)
def _frontend_origin(monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_BASE_URL", "https://app.shelfmonitor.test")


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    # The forgot-password background task cannot use a request-scoped session, so it
    # opens its own; point that at this test's database (same pattern as
    # capture_pipeline.SessionLocal in the pipeline tests).
    original = password_reset.SessionLocal
    password_reset.SessionLocal = TestingSessionLocal
    yield TestingSessionLocal
    password_reset.SessionLocal = original
    app.dependency_overrides.clear()


@pytest.fixture
def client(session_factory):
    yield TestClient(app)


@pytest.fixture
def db_session(session_factory):
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
def reset_emails(monkeypatch):
    """Capture both reset emails instead of sending them."""
    captured: dict[str, list] = {"resets": [], "notices": []}

    def fake_reset(to_email, reset_link, *, expires_minutes):
        captured["resets"].append(
            {"to": to_email, "link": reset_link, "expires_minutes": expires_minutes}
        )
        return [to_email]

    def fake_notice(to_email):
        captured["notices"].append(to_email)
        return [to_email]

    monkeypatch.setattr(email_service, "send_password_reset", fake_reset)
    monkeypatch.setattr(email_service, "send_password_changed_notice", fake_notice)
    return captured


# ─── helpers ──────────────────────────────────────────────────────────────────


def _user(db_session, *, email: str = EMAIL, password: str = PASSWORD, **overrides) -> User:
    store = Store(name=f"North {email}", location=None)
    db_session.add(store)
    db_session.flush()
    user = User(
        email=email,
        hashed_password=hash_password(password),
        store_id=store.id,
        **{"email_verified": True, **overrides},
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _enable_mfa(db_session, user: User) -> str:
    secret = mfa.generate_secret()
    mfa.store_secret(user, secret)
    user.mfa_enabled = True
    user.mfa_last_used_step = None
    db_session.commit()
    return secret


def _forgot(client, *, email: str = EMAIL, ip: str = "203.0.113.5"):
    return client.post(
        f"{BASE}/auth/forgot-password",
        json={"email": email},
        headers={"x-forwarded-for": ip},
    )


def _reset(client, token: str, *, password: str = NEW_PASSWORD, ip: str = "203.0.113.5"):
    return client.post(
        f"{BASE}/auth/reset-password",
        json={"token": token, "new_password": password},
        headers={"x-forwarded-for": ip},
    )


def _token_from_link(link: str) -> str:
    assert "token=" in link, link
    return link.split("token=", 1)[1]


def _request_token(client, reset_emails, *, email: str = EMAIL) -> str:
    resp = _forgot(client, email=email)
    assert resp.status_code == 202, resp.text
    link = reset_emails["resets"][-1]["link"]
    return _token_from_link(link)


def _login(client, *, password: str = PASSWORD, email: str = EMAIL):
    return client.post(
        f"{BASE}/auth/login", json={"email": email, "password": password}
    )


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ─── no account enumeration ───────────────────────────────────────────────────


def test_known_and_unknown_addresses_get_the_same_response(
    client, db_session, reset_emails
):
    """Same status and body, and only the known address actually gets mail."""
    _user(db_session)

    known = _forgot(client)
    unknown = _forgot(client, email="nobody@shop.com")

    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()
    assert set(known.json()) == {"status", "message"}

    # Exactly one email: the unknown address must not be prompted in any way.
    assert [m["to"] for m in reset_emails["resets"]] == [EMAIL]


def test_unknown_address_creates_no_token_row(client, db_session, reset_emails):
    db_session.add(Store(name="Empty", location=None))
    db_session.commit()

    _forgot(client, email="nobody@shop.com")

    assert db_session.query(PasswordResetToken).count() == 0
    assert reset_emails["resets"] == []


def test_demo_account_and_test_addresses_never_get_a_token(
    client, db_session, reset_emails
):
    """The published demo login and any .test address are not resettable."""
    demo = _user(db_session, email="demo@shelfmonitor.test", mfa_exempt=True)
    other = _user(db_session, email="someone@example.test")

    assert _forgot(client, email=demo.email).status_code == 202
    assert _forgot(client, email=other.email).status_code == 202

    assert reset_emails["resets"] == []
    assert db_session.query(PasswordResetToken).count() == 0


# ─── happy path ───────────────────────────────────────────────────────────────


def test_happy_path_sets_the_new_password_and_old_one_stops_working(
    client, db_session, reset_emails
):
    user = _user(db_session)
    token = _request_token(client, reset_emails)

    # The link is built from the configured origin, never from request headers.
    assert reset_emails["resets"][0]["link"].startswith(
        "https://app.shelfmonitor.test/reset-password?token="
    )

    resp = _reset(client, token)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "password_reset"

    # The account is still unenrolled, so a correct password yields an enrolment token —
    # that is the proof the password itself was accepted.
    reborn = _login(client, password=NEW_PASSWORD)
    assert reborn.status_code == 200, reborn.text
    assert reborn.json()["status"] == "mfa_setup_required"

    assert _login(client, password=PASSWORD).status_code == 401

    # And the user was told their password changed.
    assert reset_emails["notices"] == [EMAIL]

    db_session.expire_all()
    assert as_utc(db_session.get(User, user.id).password_changed_at) is not None


def test_reset_marks_the_address_verified(client, db_session, reset_emails):
    user = _user(db_session, email_verified=False)
    token = _request_token(client, reset_emails)

    assert _reset(client, token).status_code == 200

    db_session.expire_all()
    assert db_session.get(User, user.id).email_verified is True


def test_reset_response_contains_no_token_of_any_kind(client, db_session, reset_emails):
    _user(db_session)
    token = _request_token(client, reset_emails)

    body = _reset(client, token).json()

    assert set(body) == {"status", "message"}
    assert "token" not in str(body).lower()


def test_reset_does_not_bypass_mfa(client, db_session, reset_emails):
    """An enrolled account still has to produce a code after resetting."""
    user = _user(db_session)
    secret = _enable_mfa(db_session, user)
    token = _request_token(client, reset_emails)

    assert _reset(client, token).status_code == 200

    first = _login(client, password=NEW_PASSWORD)
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "mfa_required"
    assert "access_token" not in body
    assert "setup_token" not in body

    verified = client.post(
        f"{BASE}/auth/mfa/verify",
        json={
            "challenge_token": body["challenge_token"],
            "code": pyotp.TOTP(secret).at(time.time() + 30),
        },
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["access_token"]


# ─── token lifetime and storage ───────────────────────────────────────────────


def test_only_the_sha256_hash_is_stored(client, db_session, reset_emails):
    _user(db_session)
    token = _request_token(client, reset_emails)

    row = db_session.query(PasswordResetToken).one()
    assert row.token_hash == hashlib.sha256(token.encode()).hexdigest()
    assert token not in row.token_hash
    # Nothing in the row can be replayed as a link.
    assert token not in repr(row)


def test_a_token_works_once(client, db_session, reset_emails):
    _user(db_session)
    token = _request_token(client, reset_emails)

    assert _reset(client, token).status_code == 200

    second = _reset(client, token, password="another-horse")
    assert second.status_code == 400, second.text
    assert second.json()["code"] == "reset_token_invalid"


def test_an_expired_token_is_refused(client, db_session, reset_emails, monkeypatch):
    _user(db_session)
    token = _request_token(client, reset_emails)

    later = utcnow() + timedelta(
        minutes=settings.PASSWORD_RESET_TTL_MINUTES + 1
    )
    monkeypatch.setattr(password_reset, "utcnow", lambda: later)

    resp = _reset(client, token)
    assert resp.status_code == 400, resp.text
    assert "invalid or has expired" in resp.json()["detail"]


def test_a_newer_request_invalidates_the_older_link(
    client, db_session, reset_emails, monkeypatch
):
    user = _user(db_session)
    old_token = _request_token(client, reset_emails)

    # Step past the cooldown without sleeping.
    later = utcnow() + timedelta(seconds=settings.PASSWORD_RESET_COOLDOWN_SECONDS + 1)
    monkeypatch.setattr(password_reset, "utcnow", lambda: later)
    new_token = _request_token(client, reset_emails)

    assert old_token != new_token
    assert _reset(client, old_token).status_code == 400
    assert _reset(client, new_token).status_code == 200

    db_session.expire_all()
    assert db_session.query(PasswordResetToken).count() == 2
    assert (
        db_session.query(PasswordResetToken)
        .filter(PasswordResetToken.used_at.is_(None))
        .count()
        == 0
    )


# ─── session invalidation ─────────────────────────────────────────────────────


def test_an_access_token_issued_before_the_reset_is_rejected(
    client, db_session, reset_emails, monkeypatch
):
    user = _user(db_session)
    stale = create_access_token(user.id)
    assert client.get(f"{BASE}/auth/me", headers=_bearer(stale)).status_code == 200

    token = _request_token(client, reset_emails)
    # Reset a few seconds "later" so the change is strictly newer than the token's iat
    # (both are second-granular).
    later = utcnow() + timedelta(seconds=5)
    monkeypatch.setattr(password_reset, "utcnow", lambda: later)
    assert _reset(client, token).status_code == 200

    resp = client.get(f"{BASE}/auth/me", headers=_bearer(stale))
    assert resp.status_code == 401, resp.text


def test_an_mfa_challenge_issued_before_the_reset_is_rejected(
    client, db_session, reset_emails, monkeypatch
):
    user = _user(db_session)
    secret = _enable_mfa(db_session, user)

    challenge = _login(client).json()["challenge_token"]

    token = _request_token(client, reset_emails)
    later = utcnow() + timedelta(seconds=5)
    monkeypatch.setattr(password_reset, "utcnow", lambda: later)
    assert _reset(client, token).status_code == 200

    resp = client.post(
        f"{BASE}/auth/mfa/verify",
        json={
            "challenge_token": challenge,
            "code": pyotp.TOTP(secret).at(time.time() + 30),
        },
    )
    assert resp.status_code == 401, resp.text


# ─── validation, cooldown, limits, lockouts ───────────────────────────────────


def test_password_rules_match_signup(client, db_session, reset_emails):
    user = _user(db_session)
    token = _request_token(client, reset_emails)

    resp = _reset(client, token, password="short")
    assert resp.status_code == 422, resp.text

    db_session.expire_all()
    # Nothing changed: the old password still works.
    assert _login(client).status_code == 200


def test_reset_rejects_password_over_bcrypt_bytes_limit(client, db_session, reset_emails):
    """A 40-character 3-byte password is 120 bytes: bcrypt reads only the first 72."""
    _user(db_session)
    token = _request_token(client, reset_emails)
    password = "\u3042" * 40
    assert len(password.encode("utf-8")) == 120

    resp = _reset(client, token, password=password)

    assert resp.status_code == 422, resp.text
    assert "bytes" in resp.text

    db_session.expire_all()
    # The link was not consumed and the old password still works.
    assert _login(client).status_code == 200


def test_cooldown_suppresses_a_second_email_but_keeps_the_same_answer(
    client, db_session, reset_emails
):
    _user(db_session)

    first = _forgot(client)
    second = _forgot(client)

    assert first.status_code == second.status_code == 202
    assert first.json() == second.json()
    assert len(reset_emails["resets"]) == 1
    assert db_session.query(PasswordResetToken).count() == 1


def test_forgot_password_is_rate_limited_per_ip(client, db_session, reset_emails, caplog):
    _user(db_session)

    for index in range(password_reset.FORGOT_MAX_PER_IP):
        assert _forgot(client, email=f"user{index}@shop.com").status_code == 202

    with caplog.at_level(logging.INFO):
        limited = _forgot(client, email="late@shop.com")

    # Still the generic 202: a 429 here would tell the caller they were throttled but
    # nothing about the account — and the account oracle property must hold regardless.
    assert limited.status_code == 202
    assert limited.json()["status"] == "reset_requested"
    assert any("rate limited" in record.message for record in caplog.records)


def test_reset_password_attempts_are_rate_limited_per_ip(client, caplog):
    bad_token = "x" * 43

    for _ in range(password_reset.RESET_MAX_PER_IP):
        assert _reset(client, bad_token).status_code == 400

    with caplog.at_level(logging.INFO):
        resp = _reset(client, bad_token)

    assert resp.status_code == 429, resp.text
    assert resp.json()["code"] == "password_reset_rate_limited"
    assert resp.headers["Retry-After"] == str(password_reset.IP_WINDOW_SECONDS)
    assert any("rate limited" in record.message for record in caplog.records)


def test_bad_tokens_are_logged_without_the_token(client, caplog):
    bad_token = "y" * 43

    with caplog.at_level(logging.INFO):
        _reset(client, bad_token)

    messages = " ".join(record.getMessage() for record in caplog.records)
    assert "password reset failed" in messages
    assert bad_token not in messages


def test_reset_clears_a_login_lockout_but_not_an_mfa_lockout(
    client, db_session, reset_emails
):
    future = utcnow() + timedelta(minutes=10)
    user = _user(
        db_session,
        failed_login_attempts=4,
        login_locked_until=future,
        failed_mfa_attempts=3,
        mfa_locked_until=future,
    )
    token = _request_token(client, reset_emails)

    assert _reset(client, token).status_code == 200

    db_session.expire_all()
    refreshed = db_session.get(User, user.id)
    assert refreshed.failed_login_attempts == 0
    assert refreshed.login_locked_until is None
    # The second factor is untouched by a password reset.
    assert refreshed.failed_mfa_attempts == 3
    assert as_utc(refreshed.mfa_locked_until) is not None


def test_dev_flag_logs_the_link_only_without_a_transport(
    client, db_session, reset_emails, monkeypatch, caplog
):
    _user(db_session)
    monkeypatch.setattr(settings, "DEV_LOG_VERIFICATION_CODES", True)
    # Simulate "no transport at all": the send reports nothing delivered.
    monkeypatch.setattr(
        email_service, "send_password_reset", lambda *a, **k: []
    )

    with caplog.at_level(logging.WARNING):
        _forgot(client)

    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "password reset link" in warnings


def test_dev_flag_never_logs_the_link_when_a_transport_exists(
    client, db_session, reset_emails, monkeypatch, caplog
):
    _user(db_session)
    monkeypatch.setattr(settings, "DEV_LOG_VERIFICATION_CODES", True)
    # A configured relay is enough: even a failed send must not leak the link.
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(settings, "SMTP_USER", "relay@example.com")
    monkeypatch.setattr(email_service, "send_password_reset", lambda *a, **k: [])

    with caplog.at_level(logging.WARNING):
        _forgot(client)

    warnings = " ".join(r.getMessage() for r in caplog.records)
    assert "password reset link" not in warnings
    assert "could not deliver a password reset email" in warnings
