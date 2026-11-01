"""
backend/tests/test_auth_endpoints.py

Tests for the auth mechanism itself.

* POST /auth/signup  — creates store + user in one transaction and returns 202 with
  **no** token (the address is not verified yet)
* GET  /auth/me      — resolves a bearer token, 401s when missing/invalid/expired
* config             — SECRET_KEY and MFA_ENCRYPTION_KEY fail loudly in production

The multi-factor flows (verification, enrolment, challenge, recovery codes, lockouts)
live in test_mfa_auth.py; keeping them separate keeps each file readable.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.config import Settings, settings
from app.core.database import Base, get_db
from app.core.security import create_access_token, hash_password, verify_password
from app.main import app
from app.models.store import Store
from app.models.user import User

BASE = "/api/v1"

# Fixed key for the whole module so tokens survive between calls.
TEST_SECRET = "test-secret-key-with-at-least-32-chars"
TEST_FERNET = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    """Pin SECRET_KEY so tests don't depend on the developer's .env."""
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)


@pytest.fixture
def session_factory():
    """One in-memory DB per test, wired into the app for both fixtures below."""
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
    yield TestingSessionLocal
    app.dependency_overrides.clear()


@pytest.fixture
def client(session_factory):
    yield TestClient(app)


@pytest.fixture
def db_session(session_factory):
    """A direct session onto the same in-memory DB the client talks to."""
    db = session_factory()
    try:
        yield db
    finally:
        db.close()


def _signup(client, **overrides):
    payload = {
        "store_name": "North",
        "store_location": "Northville",
        "email": "owner@shop.com",
        "password": "correct-horse",
    }
    payload.update(overrides)
    return client.post(f"{BASE}/auth/signup", json=payload)


def _verified_user(db_session, email="owner@shop.com", **overrides):
    """A user row good enough to exercise token handling (no signup round trip)."""
    store = Store(name="North", location=None)
    db_session.add(store)
    db_session.flush()
    user = User(
        email=email,
        hashed_password=hash_password("correct-horse"),
        store_id=store.id,
        email_verified=True,
        **overrides,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


# ─── password hashing ─────────────────────────────────────────────────────────

def test_hash_password_never_returns_plaintext():
    hashed = hash_password("correct-horse")
    assert hashed != "correct-horse"
    assert hashed.startswith("$2")
    assert verify_password("correct-horse", hashed) is True
    assert verify_password("wrong-horse", hashed) is False


# ─── POST /auth/signup ────────────────────────────────────────────────────────

def test_signup_creates_store_and_user_but_no_session(client, db_session, verification_codes):
    resp = _signup(client)
    assert resp.status_code == 202, resp.text
    body = resp.json()

    assert body["status"] == "verification_required"
    assert body["email"] == "owner@shop.com"
    assert body["expires_in"] > 0
    # The whole point of the flow: signing up must not hand out a session.
    assert "access_token" not in body
    assert "token_type" not in body

    user = db_session.query(User).filter(User.email == "owner@shop.com").one()
    store = db_session.get(Store, user.store_id)
    assert store is not None
    assert store.name == "North"
    assert store.location == "Northville"
    assert user.email_verified is False
    assert user.mfa_enabled is False
    assert user.mfa_exempt is False
    # The plaintext password must not be anywhere in the stored record.
    assert user.hashed_password != "correct-horse"
    assert verify_password("correct-horse", user.hashed_password) is True

    # Exactly one verification email, to the address that was signed up.
    assert [m["to"] for m in verification_codes] == ["owner@shop.com"]
    assert verification_codes[0]["code"].isdigit()
    assert len(verification_codes[0]["code"]) == 6


def test_signup_duplicate_email_rejected(client, verification_codes):
    assert _signup(client).status_code == 202

    resp = _signup(client, store_name="Other", email="owner@shop.com")
    assert resp.status_code == 409, resp.text
    assert "already exists" in resp.json()["detail"].lower()


def test_signup_duplicate_email_is_case_insensitive(client, verification_codes):
    assert _signup(client, email="Owner@Shop.com").status_code == 202

    resp = _signup(client, email=" OWNER@shop.com ")
    assert resp.status_code == 409, resp.text


def test_signup_email_is_normalised(client, db_session, verification_codes):
    _signup(client, email="Owner@Shop.COM")
    assert db_session.query(User).one().email == "owner@shop.com"


def test_signup_short_password_rejected(client):
    resp = _signup(client, password="short")
    assert resp.status_code == 422, resp.text


def test_signup_multibyte_password_over_bcrypt_bytes_rejected(client):
    """40 characters of 3-byte text is 120 bytes, which bcrypt would silently truncate.

    The character check alone passes, so without a byte check the account is created
    with a password password that is really only its first 72 bytes.
    """
    password = "\u3042" * 40  # 'あ' — three bytes each in UTF-8
    assert len(password) == 40
    assert len(password.encode("utf-8")) == 120

    resp = _signup(client, password=password)

    assert resp.status_code == 422, resp.text
    assert "bytes" in resp.text


def test_signup_invalid_email_rejected(client):
    resp = _signup(client, email="not-an-email")
    assert resp.status_code == 422, resp.text


def test_signup_missing_fields_rejected(client):
    assert client.post(f"{BASE}/auth/signup", json={}).status_code == 422


def test_signup_location_is_optional(client, db_session, verification_codes):
    resp = _signup(client, store_location=None)
    assert resp.status_code == 202, resp.text
    store = db_session.get(Store, db_session.query(User).one().store_id)
    assert store.location is None


def test_signup_ignores_privileged_flags_in_the_body(client, db_session, verification_codes):
    """mfa_exempt / mfa_enabled / email_verified must not be settable by a client."""
    resp = _signup(
        client,
        mfa_exempt=True,
        mfa_enabled=True,
        email_verified=True,
    )
    assert resp.status_code == 202, resp.text

    user = db_session.query(User).one()
    assert user.mfa_exempt is False
    assert user.mfa_enabled is False
    assert user.email_verified is False


# ─── GET /auth/me ─────────────────────────────────────────────────────────────

def test_me_returns_current_user(client, db_session):
    user = _verified_user(db_session)
    token = create_access_token(user.id)

    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body == {
        "id": user.id,
        "email": "owner@shop.com",
        "store_id": user.store_id,
        "email_verified": True,
        "mfa_enabled": False,
    }
    # The response must never leak credential material.
    assert "hashed_password" not in body
    assert "password" not in body
    assert "mfa_secret_encrypted" not in body


def test_me_without_token_is_401(client):
    resp = client.get(f"{BASE}/auth/me")
    assert resp.status_code == 401, resp.text


def test_me_with_invalid_token_is_401(client):
    resp = client.get(
        f"{BASE}/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401, resp.text


def test_me_with_token_signed_by_another_key_is_401(client, db_session, monkeypatch):
    user = _verified_user(db_session)

    # Mint a token with a different signing key, then restore, so the request is
    # verified against the real key.
    monkeypatch.setattr(settings, "SECRET_KEY", "some-other-secret-key-32-chars-long!!")
    forged = create_access_token(user.id)
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)

    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401, resp.text


def test_me_with_garbage_token_is_401(client):
    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer abc.def.ghi"})
    assert resp.status_code == 401, resp.text


def test_me_with_expired_token_is_401(client, db_session):
    user = _verified_user(db_session)
    expired = create_access_token(user.id, expires_delta=timedelta(seconds=-1))

    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert resp.status_code == 401, resp.text


def test_me_with_unknown_user_id_is_401(client):
    ghost = create_access_token(999999)
    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {ghost}"})
    assert resp.status_code == 401, resp.text


def test_me_rejects_non_bearer_scheme(client):
    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": "Basic abc123"})
    assert resp.status_code == 401, resp.text


# ─── config ───────────────────────────────────────────────────────────────────

def test_missing_secret_key_fails_loudly_in_production():
    with pytest.raises(Exception) as excinfo:
        Settings(ENVIRONMENT="production", SECRET_KEY=None, _env_file=None)
    assert "SECRET_KEY" in str(excinfo.value)


def test_short_secret_key_fails_loudly_in_production():
    with pytest.raises(Exception) as excinfo:
        Settings(ENVIRONMENT="production", SECRET_KEY="too-short", _env_file=None)
    assert "too short" in str(excinfo.value)


def test_secret_key_not_required_in_development():
    assert Settings(ENVIRONMENT="development", SECRET_KEY=None, _env_file=None).SECRET_KEY is None


def test_missing_mfa_encryption_key_fails_loudly_in_production():
    with pytest.raises(Exception) as excinfo:
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY=TEST_SECRET,
            MFA_ENCRYPTION_KEY=None,
            _env_file=None,
        )
    assert "MFA_ENCRYPTION_KEY" in str(excinfo.value)


def test_malformed_mfa_encryption_key_fails_loudly():
    with pytest.raises(Exception) as excinfo:
        Settings(
            ENVIRONMENT="production",
            SECRET_KEY=TEST_SECRET,
            MFA_ENCRYPTION_KEY="not-a-fernet-key",
            _env_file=None,
        )
    assert "not a valid Fernet key" in str(excinfo.value)


def test_valid_mfa_encryption_key_is_accepted():
    built = Settings(
        ENVIRONMENT="production",
        SECRET_KEY=TEST_SECRET,
        MFA_ENCRYPTION_KEY=TEST_FERNET,
        _env_file=None,
    )
    assert built.MFA_ENCRYPTION_KEY == TEST_FERNET


def test_mfa_encryption_key_not_required_in_development():
    built = Settings(ENVIRONMENT="development", MFA_ENCRYPTION_KEY=None, _env_file=None)
    assert built.MFA_ENCRYPTION_KEY is None
