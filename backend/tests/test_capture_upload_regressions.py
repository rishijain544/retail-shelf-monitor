"""
backend/tests/test_capture_upload_regressions.py

Regression tests for the "every shelf upload fails" bug.

Production symptom: POST /captures/upload returned 500 for every image (not just
shelf-with-stock), and because the browser never received a CORS header on that 500
it surfaced as an opaque network error — so the UI looked like it silently did
nothing.

Two independent defects:

1. The initial migration created `captures.captured_at`, `shelf_reports.created_at`
   and `products.created_at` with ``server_default=sa.text('now()')``. On SQLite
   that is a literal default SQLite cannot evaluate ("unknown function: now()"), so
   every ORM insert that omitted those columns failed. `create_all()` renders
   `func.now()` as SQLite's CURRENT_TIMESTAMP, which is why the test suite (and any
   freshly created DB) never reproduced it.

2. CORSMiddleware was registered before the custom exception middleware, making it
   the *inner* layer. The JSON 500 the exception middleware returns therefore
   bypassed CORS and reached the browser without Access-Control-Allow-Origin.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.main import app
from app.models.capture import Capture
from app.models.shelf_report import ShelfReport

# The exact (broken) SQLite schema the initial migration produced.
_BROKEN_CAPTURES_DDL = """
CREATE TABLE captures (
    id INTEGER NOT NULL,
    camera_id INTEGER NOT NULL,
    image_url VARCHAR NOT NULL,
    captured_at DATETIME DEFAULT (now()) NOT NULL,
    status VARCHAR DEFAULT 'processing' NOT NULL,
    error_message VARCHAR,
    PRIMARY KEY (id)
)
"""

_BROKEN_SHELF_REPORTS_DDL = """
CREATE TABLE shelf_reports (
    id INTEGER NOT NULL,
    capture_id INTEGER NOT NULL,
    fill_percentage FLOAT NOT NULL,
    empty_regions_json JSON,
    urgency VARCHAR NOT NULL,
    llm_summary VARCHAR,
    recommended_action VARCHAR,
    created_at DATETIME DEFAULT (now()) NOT NULL,
    low_confidence BOOLEAN DEFAULT 0 NOT NULL,
    PRIMARY KEY (id)
)
"""

ORIGIN = "http://127.0.0.1:5173"


@pytest.fixture
def broken_default_engine():
    """A DB whose timestamp columns carry the Postgres-only `DEFAULT (now())`."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        conn.execute(text(_BROKEN_CAPTURES_DDL))
        conn.execute(text(_BROKEN_SHELF_REPORTS_DDL))
    return engine


def test_capture_insert_survives_postgres_only_now_default(broken_default_engine):
    """The ORM must supply captured_at itself rather than trusting the DB default."""
    session = sessionmaker(bind=broken_default_engine)()
    try:
        session.add(Capture(camera_id=1, image_url="/uploads/x.jpg"))
        session.commit()

        capture = session.query(Capture).one()
        assert capture.captured_at is not None
        assert capture.status == "processing"
    finally:
        session.close()


def test_shelf_report_insert_survives_postgres_only_now_default(broken_default_engine):
    session = sessionmaker(bind=broken_default_engine)()
    try:
        session.add(
            ShelfReport(
                capture_id=1,
                fill_percentage=0.5,
                urgency="medium",
            )
        )
        session.commit()

        report = session.query(ShelfReport).one()
        assert report.created_at is not None
    finally:
        session.close()


def test_unhandled_500_response_includes_cors_headers():
    """An error response must carry CORS headers, or the browser hides the real status."""

    async def boom() -> None:
        raise RuntimeError("boom")

    app.add_api_route("/api/v1/__boom", boom, methods=["POST"])
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/__boom", headers={"Origin": ORIGIN})
    finally:
        # Drop the throwaway route so it never leaks into another test.
        app.router.routes.pop()

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error"}
    assert response.headers.get("access-control-allow-origin") == ORIGIN
