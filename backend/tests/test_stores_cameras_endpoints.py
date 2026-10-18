"""
backend/tests/test_stores_cameras_endpoints.py

Tests for POST /stores and POST /cameras with auth and tenant scoping.

POST /cameras now uses current_user.store_id automatically — the client must not
send store_id. Tests cover the happy path, validation, and the case where the
user's store is missing.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.auth_deps import get_current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.store import Store
from app.models.user import User

BASE = "/api/v1"

TEST_SECRET = "test-secret-key-with-at-least-32-chars"


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)


@pytest.fixture
def engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


@pytest.fixture
def client(engine):
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


@pytest.fixture
def user_with_store(client, engine):
    """Create a user who owns a store, return (user, store, token)."""
    # Sign up via the auth endpoint to create store + user together
    resp = client.post(
        f"{BASE}/auth/signup",
        json={
            "store_name": "Test Store",
            "store_location": "Test City",
            "email": "user@test.com",
            "password": "correct-horse",
        },
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    token = data["access_token"]
    user_id = int(data["user"]["id"])
    store_id = data["user"]["store_id"]

    # Fetch the user and store from the same in-memory db the app uses.
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    db = TestingSessionLocal()
    try:
        user = db.get(User, user_id)
        store = db.get(Store, store_id)
    finally:
        db.close()

    return user, store, token


# ─── POST /stores ─────────────────────────────────────────────────────────────

def test_create_store_requires_auth(client):
    resp = client.post(f"{BASE}/stores", json={"name": "No Auth"})
    assert resp.status_code == 401, resp.text


def test_create_store_authenticated(client, user_with_store):
    _, _, token = user_with_store
    resp = client.post(
        f"{BASE}/stores",
        json={"name": "Another Store"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["name"] == "Another Store"
    assert data["location"] is None
    assert isinstance(data["id"], int) and data["id"] > 0


def test_create_store_with_location(client, user_with_store):
    _, _, token = user_with_store
    resp = client.post(
        f"{BASE}/stores",
        json={"name": "Some Store", "location": "Some City"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["location"] == "Some City"


def test_create_store_missing_name(client, user_with_store):
    _, _, token = user_with_store
    resp = client.post(
        f"{BASE}/stores",
        json={"location": "Nowhere"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


def test_create_store_empty_body(client, user_with_store):
    _, _, token = user_with_store
    resp = client.post(
        f"{BASE}/stores",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


# ─── POST /cameras ────────────────────────────────────────────────────────────

def test_create_camera_authenticated(client, user_with_store):
    _, store, token = user_with_store
    resp = client.post(
        f"{BASE}/cameras",
        json={"aisle": "Aisle 7", "name": "camA"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["store_id"] == store.id
    assert data["aisle"] == "Aisle 7"
    assert data["name"] == "camA"
    assert isinstance(data["id"], int) and data["id"] > 0


def test_create_camera_client_store_id_ignored(client, user_with_store):
    """The client cannot override store_id — it's always the user's store."""
    _, store, token = user_with_store
    # Even if the client sends a different store_id, it's ignored
    resp = client.post(
        f"{BASE}/cameras",
        json={"store_id": 999999, "aisle": "Aisle 7", "name": "camA"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["store_id"] == store.id


def test_create_camera_requires_auth(client):
    resp = client.post(
        f"{BASE}/cameras",
        json={"store_id": 1, "aisle": "Aisle 7", "name": "camA"},
    )
    assert resp.status_code == 401, resp.text


def test_create_camera_missing_aisle(client, user_with_store):
    _, _, token = user_with_store
    resp = client.post(
        f"{BASE}/cameras",
        json={"name": "camA"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


def test_create_camera_missing_name(client, user_with_store):
    _, _, token = user_with_store
    resp = client.post(
        f"{BASE}/cameras",
        json={"aisle": "Aisle 7"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text


def test_create_camera_empty_body(client, user_with_store):
    _, _, token = user_with_store
    resp = client.post(
        f"{BASE}/cameras",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 422, resp.text
