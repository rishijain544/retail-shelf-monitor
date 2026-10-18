"""
backend/tests/test_e2e_capture_flow.py

End-to-end integration test for the full capture upload, analysis, and report flow:
    POST /captures/upload -> background analysis -> poll GET /captures/{id}/report -> "completed"

Uses an in-memory SQLite database, mocked Anthropic Claude tool use,
mocked Slack webhook HTTP dispatch, and mocked CV analyzer.
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # Register all models on Base.metadata
from app.core.auth_deps import get_current_user
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.detection import Detection
from app.models.shelf_report import ShelfReport
from app.models.store import Store
from app.models.user import User
from app.schemas.llm import ShelfReportOutput

TEST_SECRET = "test-secret-key-with-at-least-32-chars"


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)


@pytest.fixture
def e2e_env(monkeypatch, tmp_path):
    """Setup isolated SQLite DB, mock Anthropic + Slack + CV, seed a store + user + camera."""
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
    monkeypatch.setattr("app.services.capture_pipeline.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path))
    monkeypatch.setattr(settings, "ANTHROPIC_API_KEY", "sk-ant-test-key-12345")
    monkeypatch.setattr(
        settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T00/B00/E2ETEST"
    )

    # Track Slack posts
    slack_posts: list[dict] = []

    def _fake_slack_post(url, json=None, timeout=None, **kwargs):
        slack_posts.append({"url": url, "json": json})
        return types.SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr("app.services.slack_service.httpx.post", _fake_slack_post)

    # Seed store + user + camera
    db = TestingSessionLocal()
    store = Store(name="E2E Flagship Store", location="500 Broadway, NY")
    db.add(store)
    db.flush()
    store_id = store.id

    user = User(
        email="user@e2e.test",
        hashed_password="dummy-hash",
        store_id=store_id,
    )
    db.add(user)
    db.flush()
    user_id = user.id

    camera = Camera(store_id=store_id, aisle="Aisle 3 - Breakfast", name="cam-e2e-01")
    db.add(camera)
    db.commit()
    camera_id = camera.id
    token = create_access_token(user_id)
    db.close()

    yield {
        "SessionLocal": TestingSessionLocal,
        "camera_id": camera_id,
        "store_id": store_id,
        "user_id": user_id,
        "slack_posts": slack_posts,
        "token": token,
    }

    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


def test_full_e2e_upload_poll_completed_high_urgency(e2e_env, monkeypatch, client):
    """
    Test full flow for high-urgency shelf capture:
    1. POST /captures/upload returns 202 and status="processing".
    2. Background pipeline executes CV analyzer, Anthropic LLM, and Slack alert.
    3. GET /captures/{id}/report returns status="completed" with full details.
    4. Database rows are verified.
    """
    # 1. Mock CV Analyzer
    def mock_analyze(image_path, conf=0.4, iou=0.5):
        return {
            "detections": [
                {"bbox": [10, 10, 100, 100], "confidence": 0.95, "class_name": "empty"},
                {"bbox": [110, 10, 200, 100], "confidence": 0.91, "class_name": "empty"},
                {"bbox": [210, 10, 300, 100], "confidence": 0.88, "class_name": "empty"},
            ],
            "fill_percentage": 0.22,
            "empty_regions": [
                [10, 10, 100, 100],
                [110, 10, 200, 100],
                [210, 10, 300, 100],
            ],
            "skew_angle": 0.0,
        }

    monkeypatch.setattr("app.services.analyzer_service.analyze", mock_analyze)

    # 2. Mock Anthropic API returning structured tool use
    mock_tool_block = MagicMock()
    mock_tool_block.type = "tool_use"
    mock_tool_block.input = {
        "summary": "Aisle 3 - Breakfast is critically low at 22.0% fill with 3 empty sections.",
        "urgency": "high",
        "recommended_action": "Restock cereal boxes and oats in Aisle 3 immediately.",
    }
    mock_msg_resp = MagicMock()
    mock_msg_resp.content = [mock_tool_block]

    with monkeypatch.context() as m:
        m.setattr("app.services.llm_service.anthropic.Anthropic", MagicMock(
            return_value=MagicMock(messages=MagicMock(create=MagicMock(return_value=mock_msg_resp)))
        ))

        # 3. POST /captures/upload
        upload_resp = client.post(
            "/api/v1/captures/upload",
            files={"file": ("shelf_photo.jpg", b"\xff\xd8\xff\xe0mockjpegdata", "image/jpeg")},
            data={"camera_id": str(e2e_env["camera_id"]), "conf": "0.4", "iou": "0.5"},
            headers={"Authorization": f"Bearer {e2e_env['token']}"},
        )
        assert upload_resp.status_code == 202, upload_resp.text
        upload_data = upload_resp.json()
        capture_id = upload_data["capture_id"]
        assert upload_data["status"] == "processing"
        assert upload_data["report_url"] == f"/api/v1/captures/{capture_id}/report"

        # 4. Poll GET /captures/{id}/report
        report_resp = client.get(
            f"/api/v1/captures/{capture_id}/report",
            headers={"Authorization": f"Bearer {e2e_env['token']}"},
        )
        assert report_resp.status_code == 200, report_resp.text
        report_data = report_resp.json()

        assert report_data["status"] == "completed"
        assert report_data["capture_id"] == capture_id
        assert report_data["fill_percentage"] == pytest.approx(0.22)
        assert report_data["empty_region_count"] == 3
        assert report_data["report"]["urgency"] == "high"
        assert "critically low" in report_data["report"]["summary"]
        assert "Restock cereal" in report_data["report"]["recommended_action"]
        assert len(report_data["detections"]) == 3

    # 5. Verify database state
    db = e2e_env["SessionLocal"]()
    try:
        cap = db.query(Capture).filter(Capture.id == capture_id).one()
        assert cap.status == "ready"
        assert cap.error_message is None

        dets = db.query(Detection).filter(Detection.capture_id == capture_id).all()
        assert len(dets) == 3

        rep = db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).one()
        assert rep.urgency == "high"
        assert rep.fill_percentage == pytest.approx(0.22)

        alert = db.query(Alert).filter(Alert.shelf_report_id == rep.id).one()
        assert alert.sent_to_slack is True
        assert alert.sent_at is not None
    finally:
        db.close()

    # 6. Verify Slack webhook was invoked
    posts = e2e_env["slack_posts"]
    assert len(posts) == 1
    assert "Aisle 3 - Breakfast" in posts[0]["json"]["text"]


def test_full_e2e_upload_poll_completed_low_urgency_no_alert(e2e_env, monkeypatch, client):
    """
    Test full flow for low-urgency (well stocked) shelf:
    - Verifies LLM summary and report.
    - Verifies no Alert row is recorded and Slack webhook is not triggered.
    """
    # 1. Mock CV Analyzer (high fill = 85%)
    def mock_analyze(image_path, conf=0.4, iou=0.5):
        return {
            "detections": [
                {"bbox": [10, 10, 80, 80], "confidence": 0.75, "class_name": "empty"},
            ],
            "fill_percentage": 0.85,
            "empty_regions": [[10, 10, 80, 80]],
            "skew_angle": 0.0,
        }

    monkeypatch.setattr("app.services.analyzer_service.analyze", mock_analyze)

    # 2. Mock Anthropic tool use
    mock_tool_block = MagicMock()
    mock_tool_block.type = "tool_use"
    mock_tool_block.input = {
        "summary": "Aisle 3 - Breakfast is well stocked (85.0% fill).",
        "urgency": "low",
        "recommended_action": "No immediate action required.",
    }
    mock_msg_resp = MagicMock()
    mock_msg_resp.content = [mock_tool_block]

    with monkeypatch.context() as m:
        m.setattr("app.services.llm_service.anthropic.Anthropic", MagicMock(
            return_value=MagicMock(messages=MagicMock(create=MagicMock(return_value=mock_msg_resp)))
        ))

        auth = {"Authorization": f"Bearer {e2e_env['token']}"}

        # 3. Upload capture
        upload_resp = client.post(
            "/api/v1/captures/upload",
            files={"file": ("shelf_ok.jpg", b"\xff\xd8\xff\xe0mockjpegdata", "image/jpeg")},
            data={"camera_id": str(e2e_env["camera_id"])},
            headers=auth,
        )
        assert upload_resp.status_code == 202
        capture_id = upload_resp.json()["capture_id"]

        # 4. Poll report
        report_resp = client.get(
            f"/api/v1/captures/{capture_id}/report",
            headers=auth,
        )
        assert report_resp.status_code == 200
        report_data = report_resp.json()
        assert report_data["status"] == "completed"
        assert report_data["report"]["urgency"] == "low"

    # 5. Database: No Alert rows
    db = e2e_env["SessionLocal"]()
    try:
        assert db.query(Alert).count() == 0
    finally:
        db.close()

    # 6. Slack posts: empty
    assert len(e2e_env["slack_posts"]) == 0


def test_unhandled_exception_middleware_returns_json_500(client):
    """
    Verify that unhandled exceptions during request handling return a consistent
    JSON 500 { "detail": "Internal server error" } response.
    """
    # Define a temporary route that blows up with an unhandled runtime error
    @app.get("/api/v1/test-unhandled-crash")
    def crash_route():
        raise ZeroDivisionError("Unexpected division by zero in business logic")

    response = client.get("/api/v1/test-unhandled-crash")
    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    data = response.json()
    assert data == {"detail": "Internal server error"}
