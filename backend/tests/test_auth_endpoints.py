"""
backend/tests/test_auth_endpoints.py

Tests for the auth mechanism itself.

* POST /auth/signup — creates store + user in one transaction, returns a token
* POST /auth/login  — verifies the hashed password, rejects bad credentials
* GET  /auth/me     — resolves a bearer token, 401s when missing/invalid

The existing endpoints are intentionally not exercised here: nothing is
protected yet, so these tests only cover the new /auth routes.
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


# ─── password hashing ─────────────────────────────────────────────────────────

def test_hash_password_never_returns_plaintext():
    hashed = hash_password("correct-horse")
    assert hashed != "correct-horse"
    assert hashed.startswith("$2")
    assert verify_password("correct-horse", hashed) is True
    assert verify_password("wrong-horse", hashed) is False


# ─── POST /auth/signup ────────────────────────────────────────────────────────

def test_signup_creates_store_and_user(client, db_session):
    resp = _signup(client)
    assert resp.status_code == 201, resp.text
    body = resp.json()

    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["expires_in"] > 0
    assert body["user"]["email"] == "owner@shop.com"
    assert body["user"]["store_id"] > 0

    store = db_session.get(Store, body["user"]["store_id"])
    assert store is not None
    assert store.name == "North"
    assert store.location == "Northville"

    user = db_session.query(User).filter(User.email == "owner@shop.com").one()
    assert user.store_id == store.id
    # The plaintext password must not be anywhere in the stored record.
    assert user.hashed_password != "correct-horse"
    assert verify_password("correct-horse", user.hashed_password) is True


def test_signup_token_works_on_me(client):
    token = _signup(client).json()["access_token"]

    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "owner@shop.com"


def test_signup_duplicate_email_rejected(client):
    assert _signup(client).status_code == 201

    resp = _signup(client, store_name="Other", email="owner@shop.com")
    assert resp.status_code == 409, resp.text
    assert "already exists" in resp.json()["detail"].lower()


def test_signup_duplicate_email_is_case_insensitive(client):
    assert _signup(client, email="Owner@Shop.com").status_code == 201

    resp = _signup(client, email=" OWNER@shop.com ")
    assert resp.status_code == 409, resp.text


def test_signup_email_is_normalised(client, db_session):
    _signup(client, email="Owner@Shop.COM")
    assert db_session.query(User).one().email == "owner@shop.com"


def test_signup_short_password_rejected(client):
    resp = _signup(client, password="short")
    assert resp.status_code == 422, resp.text


def test_signup_invalid_email_rejected(client):
    resp = _signup(client, email="not-an-email")
    assert resp.status_code == 422, resp.text


def test_signup_missing_fields_rejected(client):
    assert client.post(f"{BASE}/auth/signup", json={}).status_code == 422


def test_signup_location_is_optional(client, db_session):
    resp = _signup(client, store_location=None)
    assert resp.status_code == 201, resp.text
    store = db_session.get(Store, resp.json()["user"]["store_id"])
    assert store.location is None


# ─── POST /auth/login ─────────────────────────────────────────────────────────

def test_login_success(client):
    _signup(client)

    resp = client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@shop.com", "password": "correct-horse"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


def test_login_wrong_password_rejected(client):
    _signup(client)

    resp = client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@shop.com", "password": "wrong-password"},
    )
    assert resp.status_code == 401, resp.text
    assert "incorrect" in resp.json()["detail"].lower()
    assert "access_token" not in resp.json()


def test_login_unknown_email_rejected(client):
    resp = client.post(
        f"{BASE}/auth/login",
        json={"email": "nobody@shop.com", "password": "correct-horse"},
    )
    assert resp.status_code == 401, resp.text


def test_login_is_case_insensitive_on_email(client):
    _signup(client, email="owner@shop.com")

    resp = client.post(
        f"{BASE}/auth/login",
        json={"email": "Owner@Shop.com", "password": "correct-horse"},
    )
    assert resp.status_code == 200, resp.text


def test_login_token_works_on_me(client):
    _signup(client)
    token = client.post(
        f"{BASE}/auth/login",
        json={"email": "owner@shop.com", "password": "correct-horse"},
    ).json()["access_token"]

    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["email"] == "owner@shop.com"


# ─── GET /auth/me ─────────────────────────────────────────────────────────────

def test_me_returns_current_user(client):
    signup = _signup(client).json()
    token = signup["access_token"]

    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert body == {
        "id": signup["user"]["id"],
        "email": "owner@shop.com",
        "store_id": signup["user"]["store_id"],
    }
    # The response must never leak credential material.
    assert "hashed_password" not in body
    assert "password" not in body


def test_me_without_token_is_401(client):
    resp = client.get(f"{BASE}/auth/me")
    assert resp.status_code == 401, resp.text


def test_me_with_invalid_token_is_401(client):
    resp = client.get(
        f"{BASE}/auth/me",
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401, resp.text


def test_me_with_token_signed_by_another_key_is_401(client, monkeypatch):
    signup = _signup(client).json()

    # Mint a token with a different signing key, then restore, so the request is
    # verified against the real key.
    monkeypatch.setattr(settings, "SECRET_KEY", "some-other-secret-key-32-chars-long!!")
    forged = create_access_token(signup["user"]["id"])
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)

    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert resp.status_code == 401, resp.text


def test_me_with_garbage_token_is_401(client):
    resp = client.get(f"{BASE}/auth/me", headers={"Authorization": "Bearer abc.def.ghi"})
    assert resp.status_code == 401, resp.text


def test_me_with_expired_token_is_401(client, monkeypatch):
    signup = _signup(client).json()
    expired = create_access_token(
        signup["user"]["id"], expires_delta=timedelta(seconds=-1)
    )

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
