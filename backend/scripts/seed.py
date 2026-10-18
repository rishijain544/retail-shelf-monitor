"""
backend/scripts/seed.py

Database seed script to populate sample stores, cameras, captures,
detections, shelf reports, and alerts for local development and testing.

Usage:
    python backend/scripts/seed.py
    # or from backend/ directory:
    python scripts/seed.py
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw

# Put backend/ in sys.path
_BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from app.core.config import settings
from app.core.database import Base, SessionLocal, engine
import app.models  # Register all models on Base.metadata
from app.core.security import hash_password
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.detection import Detection
from app.models.shelf_report import ShelfReport
from app.models.store import Store
from app.models.user import User


def get_or_create_store(db, name: str, location: str) -> tuple[Store, bool]:
    """Return (store, created).

    Store name is the natural key for the sample data: this script only ever
    creates distinct sample stores, so a re-run reuses the existing row instead
    of cloning it.
    """
    store = db.query(Store).filter(Store.name == name).first()
    if store is not None:
        return store, False
    store = Store(name=name, location=location)
    db.add(store)
    db.flush()
    return store, True


def get_or_create_camera(db, store: Store, aisle: str, name: str) -> tuple[Camera, bool]:
    """Return (camera, created), keyed on (store_id, name) for idempotent re-runs."""
    camera = (
        db.query(Camera)
        .filter(Camera.store_id == store.id, Camera.name == name)
        .first()
    )
    if camera is not None:
        return camera, False
    camera = Camera(store_id=store.id, aisle=aisle, name=name)
    db.add(camera)
    db.flush()
    return camera, True


def render_sample_image(upload_dir: Path, index: int, boxes: list[list[int]]) -> Path:
    """Write a shelf-style placeholder JPEG for a seeded capture.

    Seeded captures point at /uploads/sample_capture_<n>.jpg. If that file is
    not on disk the frontend falls back to "Capture image could not be
    loaded.", so we synthesize a deterministic image and draw the same
    empty-region boxes the detection overlay renders on top of it.
    """
    upload_dir.mkdir(parents=True, exist_ok=True)
    width, height = 640, 480
    image = Image.new("RGB", (width, height), (208, 202, 192))
    draw = ImageDraw.Draw(image)

    # Faint grid so the placeholder reads as a shelf photo.
    for x in range(0, width, 80):
        draw.line([(x, 0), (x, height)], fill=(186, 180, 170), width=2)
    for y in range(0, height, 80):
        draw.line([(0, y), (width, y)], fill=(186, 180, 170), width=2)

    for box in boxes:
        x1, y1, x2, y2 = box
        draw.rectangle([x1, y1, x2, y2], outline=(198, 32, 32), width=4)

    path = upload_dir / f"sample_capture_{index}.jpg"
    image.save(path, "JPEG", quality=85)
    return path


def seed_database() -> None:
    """Create tables if needed and insert realistic seed data."""
    print("Initializing database tables...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # Re-running this script must be safe: sample rows that already exist are
        # reused and only missing ones are inserted, so stores/cameras are never
        # cloned. (Duplicates came from the old unconditional `db.add_all`.)
        existing_store = db.query(Store).first()
        if existing_store:
            print(f"Database already contains data (found Store: {existing_store.name}).")
            print("Reusing existing sample rows and filling in anything missing...")

        now = datetime.now(timezone.utc)

        # 1. Stores (reused on a re-run)
        store1, store1_created = get_or_create_store(
            db, "Downtown Flagship Store", "100 Main St, New York, NY"
        )
        store2, store2_created = get_or_create_store(
            db, "Westside Supermarket", "450 West End Ave, New York, NY"
        )
        stores_created = int(store1_created) + int(store2_created)
        for store, created in ((store1, store1_created), (store2, store2_created)):
            print(f"{'Created' if created else 'Reused'} Store: '{store.name}' (id={store.id})")

        # 2. Cameras (reused on a re-run, keyed on store + name)
        camera_specs = [
            (store1, "Aisle 1 - Beverages", "cam-front-bev"),
            (store1, "Aisle 4 - Snacks", "cam-mid-snack"),
            (store1, "Aisle 7 - Dairy", "cam-back-dairy"),
            (store2, "Aisle 1 - Beverages", "cam-west-bev"),
            (store2, "Aisle 12 - Produce", "cam-west-prod"),
        ]
        cameras: list[Camera] = []
        cameras_created = 0
        for store, aisle, name in camera_specs:
            camera, created = get_or_create_camera(db, store, aisle, name)
            cameras.append(camera)
            cameras_created += int(created)
        cam1, cam2, cam3, cam4, cam5 = cameras
        print(
            f"Cameras ready: {len(cameras)} "
            f"({cameras_created} created, {len(cameras) - cameras_created} reused)."
        )

        # 3. Create Captures and Shelf Reports
        sample_configs = [
            # High urgency (Beverages in store 1)
            {
                "camera": cam1,
                "time_offset_min": 5,
                "fill": 0.18,
                "urgency": "high",
                "summary": "Aisle 1 - Beverages is critically low (18.0%, 6 empty regions). Restock needed immediately.",
                "action": "Dispatch 2 pallets of bottled water and soda to Aisle 1 immediately.",
                "boxes": [
                    [50, 60, 220, 280],
                    [240, 60, 410, 280],
                    [430, 60, 600, 280],
                    [50, 320, 220, 540],
                    [240, 320, 410, 540],
                    [430, 320, 600, 540],
                ],
                "create_alert": True,
                "slack_sent": True,
            },
            # Medium urgency (Snacks in store 1)
            {
                "camera": cam2,
                "time_offset_min": 25,
                "fill": 0.48,
                "urgency": "medium",
                "summary": "Aisle 4 - Snacks fill level is moderate (48.0%, 3 empty regions). Steady depletion.",
                "action": "Schedule restocking for chip and cracker rows within the next 2 hours.",
                "boxes": [
                    [100, 150, 300, 350],
                    [320, 150, 520, 350],
                    [540, 150, 740, 350],
                ],
                "create_alert": False,
                "slack_sent": False,
            },
            # Low urgency / well-stocked (Dairy in store 1)
            {
                "camera": cam3,
                "time_offset_min": 45,
                "fill": 0.88,
                "urgency": "low",
                "summary": "Aisle 7 - Dairy is well-stocked (88.0%, 1 empty region). Stock levels stable.",
                "action": "No immediate restocking needed. Monitor at regular 4-hour cycle.",
                "boxes": [
                    [400, 100, 550, 250],
                ],
                "create_alert": False,
                "slack_sent": False,
            },
            # High urgency (Beverages in store 2 - tests cross-store same-named aisle)
            {
                "camera": cam4,
                "time_offset_min": 15,
                "fill": 0.22,
                "urgency": "high",
                "summary": "Westside Aisle 1 - Beverages is severely depleted (22.0%, 5 empty regions).",
                "action": "Urgent restock requested for juice and energy drink sections.",
                "boxes": [
                    [80, 80, 260, 260],
                    [280, 80, 460, 260],
                    [480, 80, 660, 260],
                    [80, 300, 260, 480],
                    [280, 300, 460, 480],
                ],
                "create_alert": True,
                "slack_sent": True,
            },
            # Medium urgency (Produce in store 2)
            {
                "camera": cam5,
                "time_offset_min": 60,
                "fill": 0.55,
                "urgency": "medium",
                "summary": "Aisle 12 - Produce is at 55.0% capacity with 2 empty bins.",
                "action": "Replenish organic greens and citrus bins from cold storage.",
                "boxes": [
                    [120, 180, 350, 400],
                    [380, 180, 610, 400],
                ],
                "create_alert": False,
                "slack_sent": False,
            },
        ]

        total_captures = 0
        reused_captures = 0
        total_reports = 0
        total_alerts = 0
        total_detections = 0
        upload_dir = Path(settings.UPLOAD_DIR)

        for idx, cfg in enumerate(sample_configs, start=1):
            image_url = f"/uploads/sample_capture_{idx}.jpg"
            # The deterministic image URL is the sample capture's natural key: the
            # same five captures are produced on every run, so an existing row
            # means its report and alert are already seeded too.
            if db.query(Capture).filter(Capture.image_url == image_url).first():
                reused_captures += 1
                continue

            cap_time = now - timedelta(minutes=cfg["time_offset_min"])
            # Materialize the image the capture references so the UI can show it.
            render_sample_image(upload_dir, idx, cfg["boxes"])
            cap = Capture(
                camera_id=cfg["camera"].id,
                image_url=image_url,
                status="ready",
                captured_at=cap_time,
            )
            db.add(cap)
            db.flush()
            total_captures += 1

            # Detections
            for b_idx, box in enumerate(cfg["boxes"]):
                det = Detection(
                    capture_id=cap.id,
                    class_="empty",
                    bbox=box,
                    confidence=0.88 - (b_idx * 0.04),
                )
                db.add(det)
                total_detections += 1

            # Shelf Report
            report = ShelfReport(
                capture_id=cap.id,
                fill_percentage=cfg["fill"],
                empty_regions_json=cfg["boxes"],
                urgency=cfg["urgency"],
                llm_summary=cfg["summary"],
                recommended_action=cfg["action"],
                created_at=cap_time,
            )
            db.add(report)
            db.flush()
            total_reports += 1

            # Alert if high urgency
            if cfg["create_alert"]:
                alert = Alert(
                    shelf_report_id=report.id,
                    sent_to_slack=cfg["slack_sent"],
                    sent_at=cap_time if cfg["slack_sent"] else None,
                )
                db.add(alert)
                total_alerts += 1

        # 5. Create a demo account through the same password hashing the auth
        # endpoints use, tied to the first seeded store. This is the only row the
        # script puts into `users` (the auth endpoints themselves remain the safe
        # way to add real accounts). Re-running the script must not create a second
        # demo account.
        DEMO_EMAIL = "demo@shelfmonitor.test"
        DEMO_PASSWORD = "demo-2026-shelf"
        demo_user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if demo_user is None:
            store1_db = db.query(Store).filter(Store.name == "Downtown Flagship Store").first()
            if store1_db is None:
                raise RuntimeError("Could not seed demo user: Downtown Flagship Store is missing.")
            demo_user = User(
                email=DEMO_EMAIL,
                hashed_password=hash_password(DEMO_PASSWORD),
                store_id=store1_db.id,
            )
            db.add(demo_user)
            db.flush()
            print(f"\nDemo account created: {DEMO_EMAIL} -> Store '{store1_db.name}'")
            print(f"  Password: {DEMO_PASSWORD}")
            print("  (Use src/pages/SignupPage or POST /auth/signup for your own accounts.)")
        else:
            print(f"\nDemo account already exists: {DEMO_EMAIL} (skipped).")

        db.commit()
        print("\n" + "=" * 50)
        print(">> Database seeding completed successfully!")
        print("=" * 50)
        print(f"  Stores:             {stores_created} created, {2 - stores_created} reused")
        print(f"  Cameras:            {cameras_created} created, {5 - cameras_created} reused")
        print(f"  Captures:           {total_captures} created, {reused_captures} already present")
        print(f"  Detections created: {total_detections}")
        print(f"  Reports created:    {total_reports}")
        print(f"  Alerts created:     {total_alerts}")
        print("=" * 50)

    except Exception as exc:
        db.rollback()
        print(f"Database seeding failed: {exc}", file=sys.stderr)
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed_database()
