"""
backend/tests/test_captures_endpoint.py

End-to-end tests for the capture upload + report flow with auth and tenant
scoping, with no Postgres, YOLO, or Anthropic dependency:

* DB          -> in-memory SQLite (StaticPool, shared single connection so the
                 request session and the background-task session see the same data).
* get_db      -> overridden to the SQLite session factory.
* SessionLocal in capture_pipeline -> monkeypatched to the same SQLite factory
                 (the background task opens its own session).
* analyzer_service.analyze -> monkeypatched to canned CV/YOLO output.
* generate_shelf_report    -> monkeypatched to the deterministic rule-based report
                 (so urgency is derived from fill %, no network call).

Note on timing: Starlette's TestClient runs BackgroundTasks synchronously and only
returns from client.post(...) after they finish — so by the time the POST returns,
the pipeline has already produced detections/report/alert rows.
"""

from __future__ import annotations

import types

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
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.detection import Detection
from app.models.shelf_report import ShelfReport
from app.models.store import Store
from app.models.user import User
from app.services.llm_service import _rule_based_report

TEST_SECRET = "test-secret-key-with-at-least-32-chars"


def _analysis(fill: float, n: int, **extra) -> dict:
    """Build a canned ShelfAnalyzer.analyze_shelf() result with n empty boxes."""
    boxes = [[10 + i * 120, 20, 110 + i * 120, 220] for i in range(n)]
    return {
        "detections": [
            {"bbox": b, "confidence": 0.9 - i * 0.05, "class_name": "empty"}
            for i, b in enumerate(boxes)
        ],
        "fill_percentage": fill,
        "empty_regions": boxes,
        "skew_angle": 0.0,
        **extra,
    }


# ─── Fixtures ──────────────────────────────────────────────────────────────────

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
def env(monkeypatch, client, engine):
    """Wire up isolated SQLite DB + patched pipeline collaborators, return helpers."""
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    Base.metadata.create_all(bind=engine)

    # Background task opens its own session -> point it at the same in-memory DB
    monkeypatch.setattr("app.services.capture_pipeline.SessionLocal", TestingSessionLocal)
    # LLM -> deterministic rule-based report (no Anthropic)
    monkeypatch.setattr(
        "app.services.capture_pipeline.generate_shelf_report",
        lambda data, prev=None: _rule_based_report(data, prev),
    )
    # Slack -> mock the network boundary (httpx.post) so tests never hit a real webhook
    monkeypatch.setattr(
        settings, "SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T00/B00/TEST"
    )
    slack_posts: list[dict] = []

    def _fake_slack_post(url, json=None, timeout=None, **kwargs):
        slack_posts.append({"url": url, "json": json})
        return types.SimpleNamespace(status_code=200, text="ok")

    monkeypatch.setattr("app.services.slack_service.httpx.post", _fake_slack_post)
    # Write uploaded files under a throwaway dir
    monkeypatch.setattr(settings, "UPLOAD_DIR", "/tmp/test-uploads")
    monkeypatch.setattr(settings, "UPLOAD_DIR", "/tmp/test-uploads")

    # Seed store + camera + user (capture upload now requires auth + user must own the camera)
    db = TestingSessionLocal()
    try:
        store = Store(name="Test Store", location="Testville")
        db.add(store)
        db.flush()
        store_id = store.id

        user = User(
            email="user@test.com",
            hashed_password="$2b$12$usestexamplehashnotreal",
            store_id=store_id,
        )
        db.add(user)
        db.flush()
        user_id = user.id

        camera = Camera(store_id=store_id, aisle="Aisle 7", name="cam-1")
        db.add(camera)
        db.commit()
        camera_id = camera.id
    finally:
        db.close()

    return {
        "engine": engine,
        "SessionLocal": TestingSessionLocal,
        "camera_id": camera_id,
        "store_id": store_id,
        "user_id": user_id,
        "slack_posts": slack_posts,
        "token": create_access_token(user_id),
    }


@pytest.fixture
def analyze_queue(monkeypatch):
    """Patch analyzer_service.analyze to pop canned results from a list the test fills."""
    queue: list[dict] = []

    def fake_analyze(image_path, conf=0.4, iou=0.5):
        assert queue, "analyze() called but no canned result was queued"
        return queue.pop(0)

    monkeypatch.setattr("app.services.analyzer_service.analyze", fake_analyze)
    return queue


def _upload(client, camera_id, token, *, content=b"\xff\xd8\xff\xe0fakejpeg", ctype="image/jpeg"):
    return client.post(
        "/api/v1/captures/upload",
        files={"file": ("shelf.jpg", content, ctype)},
        data={"camera_id": str(camera_id), "conf": "0.4", "iou": "0.5"},
        headers={"Authorization": f"Bearer {token}"},
    )


# ─── Tests ─────────────────────────────────────────────────────────────────────

def test_upload_high_urgency_runs_full_pipeline(env, analyze_queue, client):
    analyze_queue.append(_analysis(fill=0.18, n=2))  # < 0.30 -> high urgency

    resp = _upload(client, env["camera_id"], env["token"])
    assert resp.status_code == 202, resp.text
    body = resp.json()
    capture_id = body["capture_id"]
    assert body["status"] == "processing"
    assert body["image_url"].startswith("/uploads/")
    assert body["report_url"].endswith(f"/captures/{capture_id}/report")

    # Background task has already run: verify persisted rows
    db = env["SessionLocal"]()
    try:
        assert db.query(Detection).filter(Detection.capture_id == capture_id).count() == 2
        report = db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).one()
        assert report.urgency == "high"
        assert report.fill_percentage == pytest.approx(0.18)
        assert report.recommended_action  # populated from the LLM output
        # high urgency -> a Slack alert row. Email is off in the suite (see
        # tests/conftest.py), so the row honestly records that channel too.
        alert = db.query(Alert).filter(Alert.shelf_report_id == report.id).one()
        assert alert.sent_to_slack is True
        assert alert.sent_at is not None
        assert alert.sent_to_email is False
        assert alert.email_recipients is None
    finally:
        db.close()

    # The real Slack integration ran with only the HTTP call mocked: exactly one
    # human-readable message was posted for the high-urgency report.
    posts = env["slack_posts"]
    assert len(posts) == 1
    slack_text = posts[0]["json"]["text"]
    assert "Aisle 7" in slack_text               # aisle name
    assert "18" in slack_text                    # 18.0% fill
    assert "{" not in slack_text                 # readable text, not a JSON dump

    # GET report returns the full completed payload
    got = client.get(
        body["report_url"],
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["status"] == "completed"
    assert data["capture_id"] == capture_id
    assert data["fill_percentage"] == pytest.approx(0.18)
    assert data["empty_region_count"] == 2
    assert data["previous_fill"] is None
    assert data["report"]["urgency"] == "high"
    assert data["report"]["summary"]
    assert data["report"]["recommended_action"]
    assert len(data["detections"]) == 2


def test_upload_high_urgency_survives_unconfigured_webhook(env, analyze_queue, client, monkeypatch):
    """No SLACK_WEBHOOK_URL must not fail the pipeline.

    The capture still completes and the alert row is still persisted — just with
    sent_to_slack=False and no sent_at — so a missing webhook degrades alerting
    without losing the detection record.
    """
    monkeypatch.setattr(settings, "SLACK_WEBHOOK_URL", None)
    analyze_queue.append(_analysis(fill=0.18, n=2))  # < 0.30 -> high urgency

    resp = _upload(client, env["camera_id"], env["token"])
    assert resp.status_code == 202, resp.text
    capture_id = resp.json()["capture_id"]

    assert env["slack_posts"] == []  # never touched the network

    db = env["SessionLocal"]()
    try:
        capture = db.query(Capture).filter(Capture.id == capture_id).one()
        assert capture.status == "ready"  # pipeline completed, not "failed"

        report = db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).one()
        assert report.urgency == "high"

        # Alert row is still recorded, flagged as undelivered.
        alert = db.query(Alert).filter(Alert.shelf_report_id == report.id).one()
        assert alert.sent_to_slack is False
        assert alert.sent_at is None
    finally:
        db.close()

    # The report endpoint still serves a completed payload.
    got = client.get(
        f"/api/v1/captures/{capture_id}/report",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert got.status_code == 200, got.text
    assert got.json()["status"] == "completed"


def test_high_urgency_alert_also_emails_the_store_login_address(
    env, analyze_queue, client, monkeypatch
):
    """The same high-urgency alert must reach the store's login email, not only Slack.

    Slack is already mocked by the `env` fixture; here the SMTP boundary is mocked too
    and configured, so the test proves the pipeline looks up the store's user row and
    mails that address — and that the alert row records who received it.
    """
    messages: list[tuple] = []

    class _FakeSMTP:
        def __init__(self, host, port, timeout=None, context=None):
            self.host = host
            self.port = port

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def starttls(self, context=None):
            pass

        def login(self, user, password):
            pass

        def send_message(self, message, from_addr=None, to_addrs=None):
            messages.append((message, from_addr, to_addrs))

    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.example.test")
    monkeypatch.setattr(settings, "SMTP_PORT", 587)
    monkeypatch.setattr(settings, "SMTP_USER", "alerts@example.test")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "app-password")
    monkeypatch.setattr(settings, "SMTP_FROM", "alerts@example.test")
    monkeypatch.setattr("app.services.email_service.smtplib.SMTP", _FakeSMTP)

    analyze_queue.append(_analysis(fill=0.18, n=2))  # < 0.30 -> high urgency
    resp = _upload(client, env["camera_id"], env["token"])
    assert resp.status_code == 202, resp.text

    # Slack and email both fired for the one alert.
    assert len(env["slack_posts"]) == 1
    assert len(messages) == 1
    message, _from_addr, to_addrs = messages[0]
    assert to_addrs == ["user@test.com"]  # the store's login address
    assert "Aisle 7" in message["Subject"]
    assert "18.0% fill" in message["Subject"]

    db = env["SessionLocal"]()
    try:
        report = db.query(ShelfReport).filter(
            ShelfReport.capture_id == resp.json()["capture_id"]
        ).one()
        alert = db.query(Alert).filter(Alert.shelf_report_id == report.id).one()
        assert alert.sent_to_slack is True
        assert alert.sent_to_email is True
        assert alert.email_recipients == "user@test.com"
    finally:
        db.close()


def test_previous_fill_trend_between_captures(env, analyze_queue, client):
    # First capture: 50% -> medium, no alert
    analyze_queue.append(_analysis(fill=0.50, n=1))
    first = _upload(client, env["camera_id"], env["token"]).json()

    # Second capture on the same camera: 18% -> high
    analyze_queue.append(_analysis(fill=0.18, n=3))
    second = _upload(client, env["camera_id"], env["token"]).json()

    r1 = client.get(
        first["report_url"],
        headers={"Authorization": f"Bearer {env['token']}"},
    ).json()
    assert r1["report"]["urgency"] == "medium"
    assert r1["previous_fill"] is None  # no prior capture

    r2 = client.get(
        second["report_url"],
        headers={"Authorization": f"Bearer {env['token']}"},
    ).json()
    assert r2["report"]["urgency"] == "high"
    assert r2["previous_fill"] == pytest.approx(0.50)  # trend picked up the first capture

    # Exactly one alert overall (only the high-urgency second capture)
    db = env["SessionLocal"]()
    try:
        assert db.query(Alert).count() == 1
    finally:
        db.close()


def test_previous_fill_scoped_per_camera(env, analyze_queue, client):
    """Trend context must be per-camera: a capture never inherits another camera's
    fill %. Guards both lookups — the endpoint's `previous_fill` field and the
    pipeline's own lookup (surfaced via the rule-based 'No prior capture' summary).
    Each key assertion below would fail if the camera_id filter were dropped and the
    lookup ordered by id across all cameras.
    """
    cam1_id = env["camera_id"]

    # A second camera in a DIFFERENT aisle, same store / DB.
    db = env["SessionLocal"]()
    try:
        cam1 = db.query(Camera).filter(Camera.id == cam1_id).one()
        cam2 = Camera(store_id=cam1.store_id, aisle="Aisle 99", name="cam-2")
        db.add(cam2)
        db.commit()
        db.refresh(cam2)
        cam2_id = cam2.id
    finally:
        db.close()

    # Camera 1 gets the first capture overall (lowest id): fill 90 %.
    # n=1 rather than n=0: zero detections now flags the report low_confidence, and a
    # low-confidence report (vacuous 1.0 fill) is deliberately not usable as a baseline.
    analyze_queue.append(_analysis(fill=0.90, n=1))
    _upload(client, cam1_id, env["token"])

    # Camera 2's FIRST capture comes later (higher id). An id-only (unscoped) lookup
    # would wrongly pick up camera 1's 90 % as this capture's "previous" fill.
    analyze_queue.append(_analysis(fill=0.15, n=4))
    c2 = _upload(client, cam2_id, env["token"]).json()
    r2 = client.get(
        c2["report_url"],
        headers={"Authorization": f"Bearer {env['token']}"},
    ).json()
    assert r2["previous_fill"] is None                    # endpoint lookup: scoped
    assert "No prior capture" in r2["report"]["summary"]  # pipeline lookup: fed None

    # Camera 1's SECOND capture must see camera 1's own prior (0.90), not camera 2's.
    # (Unscoped, the most recent lower id would be camera 2's capture.)
    analyze_queue.append(_analysis(fill=0.30, n=3))
    c1b = _upload(client, cam1_id, env["token"]).json()
    r1b = client.get(
        c1b["report_url"],
        headers={"Authorization": f"Bearer {env['token']}"},
    ).json()
    assert r1b["previous_fill"] == pytest.approx(0.90)

    # Camera 2's SECOND capture must see camera 2's own prior (0.15), not camera 1's.
    analyze_queue.append(_analysis(fill=0.40, n=2))
    c2b = _upload(client, cam2_id, env["token"]).json()
    r2b = client.get(
        c2b["report_url"],
        headers={"Authorization": f"Bearer {env['token']}"},
    ).json()
    assert r2b["previous_fill"] == pytest.approx(0.15)


def test_get_report_processing_before_pipeline(env, client):
    # Insert a capture with no report (simulates the pipeline not finished)
    db = env["SessionLocal"]()
    try:
        cap = Capture(camera_id=env["camera_id"], image_url="/uploads/pending.jpg")
        db.add(cap)
        db.commit()
        db.refresh(cap)
        capture_id = cap.id
    finally:
        db.close()

    got = client.get(
        f"/api/v1/captures/{capture_id}/report",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert got.status_code == 200
    data = got.json()
    assert data["status"] == "processing"
    assert data["report"] is None
    assert data["detections"] is None


def test_get_report_unknown_capture_404(env, client):
    got = client.get(
        "/api/v1/captures/999999/report",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert got.status_code == 404


def test_upload_rejects_non_image(env, client):
    resp = client.post(
        "/api/v1/captures/upload",
        files={"file": ("notes.txt", b"hello", "text/plain")},
        data={"camera_id": str(env["camera_id"])},
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert resp.status_code == 400


def test_upload_unknown_camera_404(env, analyze_queue, client):
    resp = _upload(client, 999999, env["token"])
    assert resp.status_code == 404


def test_upload_camera_not_owned_by_user_returns_403(env, analyze_queue, client):
    """A user cannot upload to a camera they don't own — even if they guess the id."""
    # Create a second store + user + camera
    db = env["SessionLocal"]()
    try:
        store2 = Store(name="Other Store", location="Other City")
        db.add(store2)
        db.flush()
        store2_id = store2.id

        user2 = User(
            email="other@test.com",
            hashed_password="$2b$12$usestexamplehashnotreal",
            store_id=store2_id,
        )
        db.add(user2)
        db.flush()
        user2_id = user2.id

        cam2 = Camera(store_id=store2_id, aisle="Aisle 99", name="cam-other")
        db.add(cam2)
        db.commit()
        db.refresh(cam2)
        cam2_id = cam2.id
    finally:
        db.close()

    token2 = create_access_token(user2_id)

    # user2 tries to upload to store1's camera (env["camera_id"])
    resp = _upload(client, env["camera_id"], token2)
    assert resp.status_code == 403, resp.text
    assert "does not belong to your store" in resp.json()["detail"]


def test_pipeline_failure_reports_failed_with_message(env, monkeypatch, client):
    # Make the CV/YOLO step blow up so the background pipeline raises.
    def boom(image_path, conf=0.4, iou=0.5):
        raise RuntimeError("YOLO model failed to load")

    monkeypatch.setattr("app.services.analyzer_service.analyze", boom)

    # Upload still succeeds (202) — the failure happens in the background task,
    # which TestClient runs synchronously before the POST returns.
    resp = _upload(client, env["camera_id"], env["token"])
    assert resp.status_code == 202, resp.text
    capture_id = resp.json()["capture_id"]

    # The capture row records the failure instead of staying "processing".
    db = env["SessionLocal"]()
    try:
        cap = db.query(Capture).filter(Capture.id == capture_id).one()
        assert cap.status == "failed"
        assert "YOLO model failed to load" in (cap.error_message or "")
        # Nothing downstream was persisted.
        assert db.query(Detection).filter(Detection.capture_id == capture_id).count() == 0
        assert db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).count() == 0
    finally:
        db.close()

    # GET report surfaces status="failed" + the message rather than hanging.
    got = client.get(
        f"/api/v1/captures/{capture_id}/report",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["status"] == "failed"
    assert "YOLO model failed to load" in data["error_message"]
    assert data["report"] is None
    assert data["detections"] is None


def test_zero_detections_flagged_low_confidence(env, analyze_queue, client):
    """
    A capture the detector could not read must be flagged low_confidence - its numeric
    fill is the analyzer's vacuous 1.0, never presented as a confident "100% full".

    Regression guard for the bug where two completely different shelf photos (a near-empty
    decorative shelf and a fully stocked grocery shelf) both came back as 100% full with 0
    empty regions and low urgency, making a silent detector failure indistinguishable from a
    healthy shelf.
    """
    analyze_queue.append(_analysis(fill=1.0, n=0))  # detector found nothing at all

    resp = _upload(client, env["camera_id"], env["token"])
    assert resp.status_code == 202, resp.text
    capture_id = resp.json()["capture_id"]

    db = env["SessionLocal"]()
    try:
        # The capture itself succeeded - this is not an error path.
        assert db.query(Capture).filter(Capture.id == capture_id).one().status == "ready"
        assert db.query(Detection).filter(Detection.capture_id == capture_id).count() == 0

        report = db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).one()
        assert report.low_confidence is True, (
            "zero detections must flag the report as low confidence"
        )
        # The numeric fill remains (trends/CSV consumers need a clean float) — but the
        # flag is what tells consumers it is vacuous, not measured.
        assert report.fill_percentage == pytest.approx(1.0)
        # Urgency is an ordinary level (thresholds on the vacuous fill), NOT "unknown".
        assert report.urgency in ("low", "medium", "high")
        # The summary must not claim the shelf is healthy.
        assert "could not be assessed" in (report.llm_summary or "")
        assert "well-stocked" not in (report.llm_summary or "")
        # No alert: low-confidence reports are excluded from alerting even though the
        # thresholded urgency is "low" anyway.
        assert db.query(Alert).filter(Alert.shelf_report_id == report.id).count() == 0
    finally:
        db.close()

    assert env["slack_posts"] == []

    got = client.get(
        f"/api/v1/captures/{capture_id}/report",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert got.status_code == 200, got.text
    data = got.json()
    assert data["status"] == "completed"
    assert data["fill_percentage"] == pytest.approx(1.0)
    assert data["low_confidence"] is True
    assert data["empty_region_count"] == 0
    assert data["report"]["urgency"] == "low"


def test_assessed_zero_region_result_is_a_confident_full_shelf(env, analyze_queue, client):
    """
    The ml pipeline now quality-gates uploads and falls back to classical heuristics, so a
    zero-region result carrying assessed=True IS a real measurement (a fully stocked
    shelf): it must NOT be flagged low_confidence, and its ordinary low urgency stands.
    """
    analyze_queue.append(_analysis(fill=0.97, n=0, assessed=True, detector="heuristic"))

    resp = _upload(client, env["camera_id"], env["token"])
    assert resp.status_code == 202, resp.text
    capture_id = resp.json()["capture_id"]

    db = env["SessionLocal"]()
    try:
        report = db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).one()
        assert report.low_confidence is False
        assert report.fill_percentage == pytest.approx(0.97)
        assert report.urgency == "low"
        # The summary is the ordinary well-stocked wording, not the unassessed one.
        assert "could not be assessed" not in (report.llm_summary or "")
    finally:
        db.close()

    got = client.get(
        f"/api/v1/captures/{capture_id}/report",
        headers={"Authorization": f"Bearer {env['token']}"},
    )
    assert got.status_code == 200, got.text
    assert got.json()["low_confidence"] is False


def test_low_confidence_report_is_not_a_trend_baseline(env, analyze_queue, client):
    """A flagged report must not feed its vacuous 1.0 into the next capture's trend
    comparison — via either lookup (endpoint's previous_fill / pipeline's summary)."""
    # 1st capture: zero detections -> flagged, vacuous fill 1.0.
    analyze_queue.append(_analysis(fill=1.0, n=0))
    first = _upload(client, env["camera_id"], env["token"]).json()

    # 2nd capture: a real measurement right after it.
    analyze_queue.append(_analysis(fill=0.5, n=2))
    second = _upload(client, env["camera_id"], env["token"]).json()

    # The endpoint's lookup skips the flagged row (no measurement to inherit).
    r2 = client.get(
        second["report_url"],
        headers={"Authorization": f"Bearer {env["token"]}"},
    ).json()
    assert r2["previous_fill"] is None
    assert "No prior capture" in r2["report"]["summary"]

    # And the first report is flagged the whole way through.
    r1 = client.get(
        first["report_url"],
        headers={"Authorization": f"Bearer {env["token"]}"},
    ).json()
    assert r1["low_confidence"] is True
    assert r1["fill_percentage"] == pytest.approx(1.0)


def test_assessed_capture_is_unaffected_by_the_unassessed_path(env, analyze_queue, client):
    """A normal detection run records a real fill level, a normal urgency, and no flag."""
    analyze_queue.append(_analysis(fill=0.62, n=1))  # >= 0.60 -> low urgency

    resp = _upload(client, env["camera_id"], env["token"])
    capture_id = resp.json()["capture_id"]

    db = env["SessionLocal"]()
    try:
        report = db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).one()
        assert report.fill_percentage == pytest.approx(0.62)
        assert report.urgency == "low"
        assert report.low_confidence is False
    finally:
        db.close()


def test_low_confidence_report_never_alerts_even_at_high_urgency(env, analyze_queue, client):
    """
    A zero-detection capture is flagged no matter what urgency it inherits. Today the
    unassessed path thresholds the vacuous 1.0 fill to "low", but a bulk-simulated case
    proves the pipeline itself excludes flagged reports from alerting — a report we are
    not confident about must never page anyone, even if its urgency reads "high".
    """
    # Zero detections but a LOW vacuous fill — the rule-based path would call this
    # "high" if the flag were not wired. Simulates a detector blindness where the
    # model also sees phantom empty space (fill 0.2 from empty_regions, no boxes kept).
    analyze_queue.append(_analysis(fill=0.2, n=0))

    resp = _upload(client, env["camera_id"], env["token"])
    capture_id = resp.json()["capture_id"]

    db = env["SessionLocal"]()
    try:
        report = db.query(ShelfReport).filter(ShelfReport.capture_id == capture_id).one()
        assert report.low_confidence is True
        assert report.urgency == "high"  # the alarm would fire if not suppressed
        # The exclude-from-alerting guard: no Alert row and no Slack post.
        assert db.query(Alert).filter(Alert.shelf_report_id == report.id).count() == 0
    finally:
        db.close()

    assert env["slack_posts"] == []
