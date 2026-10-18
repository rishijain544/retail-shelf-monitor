"""
backend/tests/test_read_endpoints.py

Tests for the read-only resource endpoints with auth and tenant scoping.
Now every data endpoint (reports, alerts, cameras, stores) requires a valid
Bearer token, and data is scoped to the current user's store.

Seeded data (two stores, each with their own user and data)
-------------------------------------------------------
Stores:   S1 "North" (user U1), S2 "South" (user U2)
Cameras:  camA (S1, Aisle 7), camB (S1, Aisle 8), camC (S2, Aisle 1)
Reports:  r1 camA Aisle 7 high 2026-09-01
          r2 camA Aisle 7 medium 2026-09-02
          r3 camB Aisle 8 low 2026-09-03
          r4 camC Aisle 1 high 2026-09-04
Alerts:   one per high-urgency report -> r1, r4 (2 total)
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  (register every table on Base.metadata)
from app.core.auth_deps import get_current_user
from app.core.auth_deps import get_current_user as _get_current_user
from app.core.auth_deps import _assert_camera_belongs_to_user as _ca
from app.core.auth_deps import _assert_camera_belongs_to_user as _assert
from app.core.auth_deps import _assert_camera_belongs_to_user as _assert_camera
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import create_access_token
from app.main import app
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.shelf_report import ShelfReport
from app.models.store import Store
from app.models.user import User

BASE = "/api/v1"

TEST_SECRET = "test-secret-key-with-at-least-32-chars"

# Fixed tokens for the two seeded users. Must match TEST_SECRET above and the
# module-level SECRET_KEY override in conftest-style fixtures below.


@pytest.fixture(autouse=True)
def _secret_key(monkeypatch):
    """Pin SECRET_KEY so tokens we mint are accepted by the real endpoint deps."""
    monkeypatch.setattr(settings, "SECRET_KEY", TEST_SECRET)


@pytest.fixture
def seeded(monkeypatch):
    """Wire an in-memory SQLite DB, seed the two-tenant dataset with users, yield
    lookup ids and auth headers for both tenants."""
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

    db = TestingSessionLocal()
    try:
        # Create stores + their owner users
        s1 = Store(name="North", location="Northville")
        s2 = Store(name="South", location="Southville")
        db.add_all([s1, s2])
        db.flush()

        u1 = User(email="owner@north.test", hashed_password="h1", store_id=s1.id)
        u2 = User(email="owner@south.test", hashed_password="h2", store_id=s2.id)
        db.add_all([u1, u2])
        db.flush()

        cam_a = Camera(store_id=s1.id, aisle="Aisle 7", name="camA")
        cam_b = Camera(store_id=s1.id, aisle="Aisle 8", name="camB")
        cam_c = Camera(store_id=s2.id, aisle="Aisle 1", name="camC")
        db.add_all([cam_a, cam_b, cam_c])
        db.flush()

        rows = [
            (cam_a, "high", 0.20, datetime(2026, 9, 1, 10, 0, 0)),
            (cam_a, "medium", 0.45, datetime(2026, 9, 2, 10, 0, 0)),
            (cam_b, "low", 0.80, datetime(2026, 9, 3, 10, 0, 0)),
            (cam_c, "high", 0.15, datetime(2026, 9, 4, 10, 0, 0)),
        ]
        report_ids: list[int] = []
        for cam, urgency, fill, created in rows:
            cap = Capture(camera_id=cam.id, image_url="/uploads/x.jpg", status="ready")
            db.add(cap)
            db.flush()
            rep = ShelfReport(
                capture_id=cap.id,
                fill_percentage=fill,
                urgency=urgency,
                llm_summary=f"{urgency} report",
                recommended_action="restock" if urgency == "high" else "monitor",
                created_at=created,
            )
            db.add(rep)
            db.flush()
            report_ids.append(rep.id)
            if urgency == "high":
                db.add(Alert(shelf_report_id=rep.id, sent_to_slack=True, sent_at=created))
        db.commit()

        # Tokens
        t1 = create_access_token(u1.id)
        t2 = create_access_token(u2.id)

        ids = {
            "store1": s1.id,
            "store2": s2.id,
            "camera1a": cam_a.id,
            "camera1b": cam_b.id,
            "camera2": cam_c.id,
            "reports1": [report_ids[0], report_ids[1], report_ids[2]],  # r1, r2, r3
            "reports2": [report_ids[3]],  # r4
            "all_reports": report_ids,
            "u1_id": u1.id,
            "u2_id": u2.id,
        }
    finally:
        db.close()

    yield ids, {"t1": t1, "t2": t2}

    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app)


def _auth_headers(token: str):
    return {"Authorization": f"Bearer {token}"}


# ─── stores / cameras ────────────────────────────────────────────────────────

def test_list_stores_requires_auth(seeded, client):
    resp = client.get(f"{BASE}/stores")
    assert resp.status_code == 401, resp.text


def test_list_stores_authenticated(seeded, client):
    ids, tokens = seeded
    resp = client.get(f"{BASE}/stores", headers=_auth_headers(tokens["t1"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert [s["name"] for s in data] == ["North", "South"]


def test_list_cameras_requires_auth(seeded, client):
    resp = client.get(f"{BASE}/cameras")
    assert resp.status_code == 401, resp.text


def test_list_cameras_authenticated_sees_only_own_store(seeded, client):
    ids, tokens = seeded
    # store1's token should only see store1's cameras (camA, camB) - not camC
    resp = client.get(f"{BASE}/cameras", headers=_auth_headers(tokens["t1"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert {c["name"] for c in data} == {"camA", "camB"}
    assert len(data) == 2


def test_list_cameras_store2_sees_only_own_cameras(seeded, client):
    ids, tokens = seeded
    resp = client.get(f"{BASE}/cameras", headers=_auth_headers(tokens["t2"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert {c["name"] for c in data} == {"camC"}
    assert len(data) == 1


# ─── Multi-tenancy isolation: the most important tests ────────────────────────

def test_store_a_user_cannot_see_store_b_reports(seeded, client):
    """store1's token cannot see store2's reports even by fetching all reports."""
    ids, tokens = seeded
    resp = client.get(f"{BASE}/reports", headers=_auth_headers(tokens["t1"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 3  # r1, r2, r3 belong to store1
    assert {i["id"] for i in data["items"]} == {ids["reports1"][0], ids["reports1"][1], ids["reports1"][2]}


def test_store_a_user_cannot_see_store_b_reports_by_id(seeded, client):
    """store1's token 404s when asking for store2's report by id."""
    ids, tokens = seeded
    resp = client.get(
        f"{BASE}/reports/{ids['reports2'][0]}",
        headers=_auth_headers(tokens["t1"]),
    )
    assert resp.status_code == 404, resp.text


def test_store_b_user_sees_only_own_reports(seeded, client):
    ids, tokens = seeded
    resp = client.get(f"{BASE}/reports", headers=_auth_headers(tokens["t2"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["total"] == 1  # only r4
    assert data["items"][0]["id"] == ids["reports2"][0]


def test_store_a_user_cannot_see_store_b_alerts(seeded, client):
    ids, tokens = seeded
    resp = client.get(f"{BASE}/alerts", headers=_auth_headers(tokens["t1"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # store1 has one alert (for r1, high urgency)
    assert data["total"] == 1
    assert data["items"][0]["store_id"] == ids["store1"]


def test_store_b_user_sees_only_own_alerts(seeded, client):
    ids, tokens = seeded
    resp = client.get(f"{BASE}/alerts", headers=_auth_headers(tokens["t2"]))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # store2 has one alert (for r4, high urgency)
    assert data["total"] == 1
    assert data["items"][0]["store_id"] == ids["store2"]


# ─── cameras: auth-gated, scoped to user's store ─────────────────────────────



# ─── reports: pagination ──────────────────────────────────────────────────────

def test_list_reports_pagination_authenticated(seeded, client):
    ids, tokens = seeded
    page1 = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
        params={"limit": 2, "offset": 0},
    ).json()
    assert page1["total"] == 3  # r1, r2, r3 belong to store1
    page2 = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
        params={"limit": 2, "offset": 2},
    ).json()
    assert [i["id"] for i in page2["items"]] == [ids["reports1"][0]]


# ─── reports: filtering (only by aisle/urgency/date, store comes from token) ─

def test_list_reports_filter_by_aisle_authenticated(seeded, client):
    ids, tokens = seeded
    data = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
        params={"aisle": "Aisle 7"},
    ).json()
    assert data["total"] == 2  # r1, r2


def test_list_reports_filter_by_urgency_authenticated(seeded, client):
    ids, tokens = seeded
    data = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
        params={"urgency": "high"},
    ).json()
    assert data["total"] == 1  # r1
    assert data["items"][0]["id"] == ids["reports1"][0]


def test_list_reports_filter_by_date_range_authenticated(seeded, client):
    ids, tokens = seeded
    data = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
        params={"start_date": "2026-09-02T00:00:00", "end_date": "2026-09-03T23:59:59"},
    ).json()
    assert data["total"] == 2  # r2, r3


def test_list_reports_combined_filters_authenticated(seeded, client):
    ids, tokens = seeded
    data = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
        params={"aisle": "Aisle 7", "urgency": "high"},
    ).json()
    assert data["total"] == 1
    assert data["items"][0]["id"] == ids["reports1"][0]


# ─── reports: single item ─────────────────────────────────────────────────────

def test_get_report_by_id_authenticated(seeded, client):
    ids, tokens = seeded
    resp = client.get(
        f"{BASE}/reports/{ids['reports1'][0]}",
        headers=_auth_headers(tokens["t1"]),
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["id"] == ids["reports1"][0]
    assert data["urgency"] == "high"


def test_get_report_unknown_404_authenticated(seeded, client):
    ids, tokens = seeded
    resp = client.get(
        f"{BASE}/reports/999999",
        headers=_auth_headers(tokens["t1"]),
    )
    assert resp.status_code == 404


# ─── reports: capture context ─────────────────────────────────────────────────

def test_list_reports_includes_capture_context_authenticated(seeded, client):
    ids, tokens = seeded
    items = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
    ).json()["items"]

    # Newest first in store1 -> r3 (camB, Aisle 8)
    newest = items[0]
    assert newest["id"] == ids["reports1"][2]
    assert newest["store_id"] == ids["store1"]
    assert newest["store_name"] == "North"
    assert newest["aisle"] == "Aisle 8"
    assert newest["camera_name"] == "camB"

    assert all(i["store_name"] and i["aisle"] and i["camera_id"] for i in items)


def test_get_report_includes_capture_context_authenticated(seeded, client):
    ids, tokens = seeded
    data = client.get(
        f"{BASE}/reports/{ids['reports1'][0]}",
        headers=_auth_headers(tokens["t1"]),
    ).json()
    assert data["store_id"] == ids["store1"]
    assert data["store_name"] == "North"
    assert data["aisle"] == "Aisle 7"
    assert data["camera_name"] == "camA"


def test_report_context_matches_own_store_only(seeded, client):
    ids, tokens = seeded
    items = client.get(
        f"{BASE}/reports",
        headers=_auth_headers(tokens["t1"]),
    ).json()["items"]
    assert len(items) == 3
    assert {i["store_name"] for i in items} == {"North"}
    assert {i["aisle"] for i in items} == {"Aisle 7", "Aisle 8"}


# ─── reports: CSV export ─────────────────────────────────────────────────────

def test_export_reports_csv_authenticated(seeded, client):
    ids, tokens = seeded
    resp = client.get(
        f"{BASE}/reports/export",
        headers=_auth_headers(tokens["t2"]),  # store2 -> only r4
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"].startswith("attachment; filename=")

    lines = resp.text.splitlines()
    assert lines[0].split(",") == [
        "store_name", "aisle", "camera_name", "fill_percentage", "low_confidence",
        "urgency", "llm_summary", "recommended_action", "created_at",
    ]
    data_lines = lines[1:]
    assert len(data_lines) == 1
    assert data_lines[0].split(",")[:4] == ["South", "Aisle 1", "camC", "0.15"]
    assert data_lines[0].split(",")[4] == "false"


def test_export_reports_csv_filtered_by_aisle_authenticated(seeded, client):
    ids, tokens = seeded
    resp = client.get(
        f"{BASE}/reports/export",
        headers=_auth_headers(tokens["t1"]),
        params={"aisle": "Aisle 7"},
    )
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    assert len(lines) - 1 == 2  # r1, r2


def test_export_reports_csv_filtered_by_date_authenticated(seeded, client):
    ids, tokens = seeded
    resp = client.get(
        f"{BASE}/reports/export",
        headers=_auth_headers(tokens["t1"]),
        params={"start_date": "2026-09-02T00:00:00", "end_date": "2026-09-03T23:59:59"},
    )
    assert resp.status_code == 200
    lines = resp.text.splitlines()
    assert len(lines) - 1 == 2  # r2, r3


# ─── alerts ───────────────────────────────────────────────────────────────────

def test_list_alerts_pagination_authenticated(seeded, client):
    ids, tokens = seeded
    data = client.get(
        f"{BASE}/alerts",
        headers=_auth_headers(tokens["t1"]),
        params={"limit": 1, "offset": 0},
    ).json()
    assert data["total"] == 1
    assert data["items"][0]["sent_to_slack"] is True


def test_list_alerts_includes_report_and_camera_context_authenticated(seeded, client):
    ids, tokens = seeded
    items = client.get(
        f"{BASE}/alerts",
        headers=_auth_headers(tokens["t1"]),
    ).json()["items"]
    assert len(items) == 1
    a = items[0]
    assert a["shelf_report_id"] == ids["reports1"][0]  # r1 (high) -> alert
    assert a["store_id"] == ids["store1"]
    assert a["store_name"] == "North"
    assert a["aisle"] == "Aisle 7"
    assert a["camera_name"] == "camA"
    assert a["urgency"] == "high"
    assert a["llm_summary"] == "high report"
    assert a["report_created_at"] is not None
