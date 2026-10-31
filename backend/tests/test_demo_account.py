"""
backend/tests/test_demo_account.py

The sign-in page advertises ``demo@shelfmonitor.test / demo-2026-shelf`` and a "Try
Demo Account" button. Nothing used to create that row outside the developer seed
script, so the advertised credentials answered 401 on a fresh deployment. These tests
pin the behaviour: provisioning is idempotent and the advertised password really
authenticates through POST /auth/login.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.config import settings
from app.core.database import Base, get_db
from app.main import app
from app.models.store import Store
from app.models.user import User
from app.services import demo_account

BASE = "/api/v1"


@pytest.fixture
def db_session(monkeypatch):
    """An in-memory SQLite session with the full schema, wired into the app."""
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret-key-with-at-least-32-chars")
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
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        app.dependency_overrides.clear()


def test_ensure_demo_account_creates_one_store_and_one_user(db_session):
    assert demo_account.ensure_demo_account(db_session) is True

    users = db_session.query(User).filter(User.email == demo_account.DEMO_EMAIL).all()
    assert len(users) == 1
    assert users[0].store_id is not None
    assert db_session.query(Store).filter(Store.name == demo_account.DEMO_STORE_NAME).count() == 1


def test_ensure_demo_account_is_idempotent(db_session):
    """A re-deploy must not clone the demo store or reset the account's id."""
    demo_account.ensure_demo_account(db_session)
    first = db_session.query(User).filter(User.email == demo_account.DEMO_EMAIL).one()

    assert demo_account.ensure_demo_account(db_session) is False

    assert db_session.query(User).filter(User.email == demo_account.DEMO_EMAIL).count() == 1
    assert db_session.query(Store).count() == 1
    assert db_session.query(User).filter(User.email == demo_account.DEMO_EMAIL).one().id == first.id


def test_advertised_demo_credentials_log_in(db_session):
    """The exact pair shown on the sign-in page must authenticate."""
    demo_account.ensure_demo_account(db_session)

    with TestClient(app) as client:
        resp = client.post(
            f"{BASE}/auth/login",
            json={"email": demo_account.DEMO_EMAIL, "password": demo_account.DEMO_PASSWORD},
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["access_token"]
    assert body["user"]["email"] == demo_account.DEMO_EMAIL
