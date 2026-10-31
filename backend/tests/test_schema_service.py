"""
backend/tests/test_schema_service.py

Regression tests for the deployment failure ``app/services/schema.py`` exists to
prevent: the API started on a host that never ran the container ENTRYPOINT, so the
database had no tables. ``GET /health`` still answered 200 because ``SELECT 1`` needs
no schema, while every signup and login returned 500 "Internal server error" with
``relation "users" does not exist``.

The app now applies the migration chain itself at startup, so these tests assert that
the chain really does build the auth tables and that the health string tells an
operator which state the database is in.

The second deployed failure these tests pin: the startup upgrade used to be gated on
``ENVIRONMENT=production``, so a service created outside ``render.yaml`` (no
``ENVIRONMENT`` at all) never migrated and every login answered 500.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

import app.main as main_module
import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.config import settings
from app.core.database import Base, get_db
from app.services import demo_account, schema

BASE = "/api/v1"


@pytest.fixture
def sqlite_url(monkeypatch, tmp_path) -> str:
    """A throwaway SQLite file the Alembic config is pointed at."""
    url = f"sqlite:///{tmp_path / 'schema.db'}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)
    return url


def test_upgrade_to_head_creates_the_auth_tables(sqlite_url):
    """A fresh database must gain the tables signup/login query."""
    schema.upgrade_to_head()

    engine = create_engine(sqlite_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "stores" in tables
        assert "users" in tables
        assert "alembic_version" in tables

        with sessionmaker(bind=engine)() as db:
            assert schema.schema_status(db) == "head"
    finally:
        engine.dispose()


def test_upgrade_to_head_is_idempotent(sqlite_url):
    """Every deploy re-runs this, so a second call must be a no-op, not an error."""
    schema.upgrade_to_head()
    schema.upgrade_to_head()

    engine = create_engine(sqlite_url)
    try:
        with sessionmaker(bind=engine)() as db:
            assert schema.schema_status(db) == "head"
    finally:
        engine.dispose()


def test_schema_status_flags_a_database_built_without_migrations(tmp_path):
    """``create_all`` databases look migrated but are not — say so instead of "head"."""
    engine = create_engine(f"sqlite:///{tmp_path / 'plain.db'}")
    Base.metadata.create_all(bind=engine)
    try:
        with sessionmaker(bind=engine)() as db:
            assert schema.schema_status(db).startswith("not_initialised")
    finally:
        engine.dispose()


def test_lifespan_migrates_a_fresh_database_outside_production(sqlite_url, monkeypatch):
    """A deployment with no ENVIRONMENT must still boot against a migrated schema.

    Reproduces the reported failure end to end: an empty database, ``ENVIRONMENT`` left
    at its development default, and a sign-in that answered 500 because no migration
    had ever run. Startup has to create the tables, and the advertised demo account has
    to exist, or the sign-in page's own credentials fail.
    """
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "AUTO_MIGRATE", True)
    monkeypatch.setattr(settings, "SEED_DEMO_ACCOUNT", True)
    monkeypatch.setattr(settings, "SECRET_KEY", "test-secret-key-with-at-least-32-chars")

    engine = create_engine(sqlite_url)
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    # The lifespan opens its own sessions (demo account + saved email settings); point
    # them at the throwaway database instead of the developer's real one.
    monkeypatch.setattr(main_module, "SessionLocal", TestingSessionLocal)
    app = main_module.app
    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as client:
            login = client.post(
                f"{BASE}/auth/login",
                json={
                    "email": demo_account.DEMO_EMAIL,
                    "password": demo_account.DEMO_PASSWORD,
                },
            )
            health = client.get(f"{BASE}/health")
    finally:
        app.dependency_overrides.clear()
        engine.dispose()

    assert login.status_code == 200, login.text
    assert login.json()["user"]["email"] == demo_account.DEMO_EMAIL
    assert health.json()["services"]["schema"] == "head"


def test_head_revision_points_at_the_latest_migration():
    """Guard against a broken chain: an empty head means nothing would be applied."""
    assert schema.head_revision() == "c9f4a2d6e8b1"
