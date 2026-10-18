"""
backend/tests/test_timestamps.py

Timestamp serialization: every API timestamp must carry an explicit UTC offset.

SQLite stores `DateTime(timezone=True)` values without the offset, so a capture taken
at 14:59 IST was serialized as "2026-10-17T09:29:52" and the browser read it as 09:29
local — three and a half hours of "wrong upload time" in the dashboard.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.shelf_report import ShelfReport
from app.models.store import Store
from app.models.user import User
from app.schemas.alert import AlertResponse
from app.schemas.capture import CaptureResponse, CaptureUploadResponse
from app.schemas.shelf_report import ShelfReportResponse


def test_naive_database_timestamp_serializes_as_explicit_utc():
    """A tz-naive datetime (what SQLite returns) is UTC, not local time."""
    report = ShelfReportResponse(
        id=1,
        capture_id=1,
        fill_percentage=0.13,
        urgency="high",
        created_at=datetime(2026, 10, 17, 9, 29, 52),
    )

    assert report.model_dump_json().count('"created_at":"2026-10-17T09:29:52Z"') == 1


def test_aware_non_utc_timestamp_is_normalized_to_utc():
    """An offset-aware value is converted, never merely relabelled."""
    ist = timezone(timedelta(hours=5, minutes=30))
    capture = CaptureResponse(
        id=1,
        camera_id=1,
        image_url="/uploads/x.png",
        captured_at=datetime(2026, 10, 17, 14, 59, 52, tzinfo=ist),
    )

    assert capture.model_dump(mode="json")["captured_at"] == "2026-10-17T09:29:52Z"


def test_optional_timestamps_stay_null():
    alert = AlertResponse(id=1, shelf_report_id=1, sent_at=None, report_created_at=None)

    payload = alert.model_dump(mode="json")
    assert payload["sent_at"] is None
    assert payload["report_created_at"] is None


def test_upload_response_timestamp_has_offset():
    resp = CaptureUploadResponse(
        capture_id=5,
        status="processing",
        image_url="/uploads/x.png",
        captured_at=datetime(2026, 10, 17, 9, 29, 52),
        report_url="/api/v1/captures/5/report",
    )

    assert resp.model_dump(mode="json")["captured_at"].endswith("Z")


def test_reports_api_returns_timestamps_with_an_offset():
    """The wire format is what the browser parses — assert on the actual response."""
    from app.core.config import settings

    settings.SECRET_KEY = settings.SECRET_KEY or "test-secret-key-with-at-least-32-chars"

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
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
    try:
        db = TestingSessionLocal()
        try:
            store = Store(name="UTC Store")
            db.add(store)
            db.flush()
            user = User(
                email="utc@test.com", hashed_password="$2b$12$notreal", store_id=store.id
            )
            db.add(user)
            db.flush()
            camera = Camera(store_id=store.id, aisle="Aisle 1", name="cam")
            db.add(camera)
            db.flush()
            capture = Capture(
                camera_id=camera.id,
                image_url="/uploads/x.png",
                captured_at=datetime(2026, 10, 17, 9, 29, 52),
            )
            db.add(capture)
            db.flush()
            report = ShelfReport(
                capture_id=capture.id,
                fill_percentage=0.13,
                urgency="high",
                created_at=datetime(2026, 10, 17, 9, 29, 56),
            )
            db.add(report)
            db.commit()
            token = create_access_token(user.id)
        finally:
            db.close()

        resp = TestClient(app).get(
            "/api/v1/reports", headers={"Authorization": f"Bearer {token}"}
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        items = body["items"] if isinstance(body, dict) else body
        assert items, body
        assert items[0]["created_at"] == "2026-10-17T09:29:56Z"
    finally:
        app.dependency_overrides.clear()
