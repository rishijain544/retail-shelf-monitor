"""
backend/app/services/capture_pipeline.py

Background pipeline for a single capture:
    CV/YOLO analysis -> persist detections -> LLM shelf report -> (high) Slack alert.

Invoked via FastAPI BackgroundTasks (add_task), so it runs in a threadpool thread AFTER
the upload response has been sent. It opens its OWN DB session — the request-scoped
session from get_db is already closed by the time this runs.

Written as a plain sync function so it can later be wrapped verbatim as a Celery task.
`SessionLocal`, `analyzer_service`, and `generate_shelf_report` are referenced at module
scope so tests can monkeypatch them without touching real DB / YOLO / Anthropic.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.alert import Alert
from app.models.capture import Capture
from app.models.detection import Detection
from app.models.shelf_report import ShelfReport
from app.models.user import User
from app.services import analyzer_service, email_service, slack_service
from app.services.llm_service import generate_shelf_report

logger = logging.getLogger(__name__)


def _resolve_disk_path(image_url: str) -> str:
    """Map a stored image_url (e.g. '/uploads/<file>') back to its file on disk."""
    return os.path.join(settings.UPLOAD_DIR, os.path.basename(image_url))


def process_capture(
    capture_id: int,
    conf: float = analyzer_service.DEFAULT_CONF_THRESHOLD,
    iou: float = analyzer_service.DEFAULT_IOU_THRESHOLD,
) -> None:
    """Run the full analysis + reporting pipeline for one capture id."""
    db = SessionLocal()
    try:
        capture = db.query(Capture).filter(Capture.id == capture_id).first()
        if capture is None:
            logger.error("process_capture: capture %s not found", capture_id)
            return

        camera = capture.camera  # relationship -> Camera (supplies aisle name)
        aisle = camera.aisle if camera else "Unknown Aisle"
        image_path = _resolve_disk_path(capture.image_url)

        # 1. CV + YOLO analysis (imported from ml/, not reimplemented)
        result = analyzer_service.analyze(image_path, conf=conf, iou=iou)
        detections = result.get("detections", []) or []
        empty_regions = result.get("empty_regions", []) or []
        detection_count = int(result.get("detection_count", len(detections)))

        # Zero detections only means "no usable evidence" when the analyzer did not
        # actually assess the image. The ml pipeline now quality-gates uploads and falls
        # back to classical CV heuristics when YOLO has no signal, returning
        # assessed=True with a real (possibly zero-region, high-fill) measurement for
        # valid shelf photos. Legacy/canned results without the flag — and any future
        # path that gives up — keep the honest behaviour: flag the row low_confidence so
        # UI and alerting treat it as "unable to assess" rather than a confident 100%
        # full shelf.
        assessed = bool(result.get("assessed", False))
        low_confidence = detection_count == 0 and not assessed
        fill_percentage = float(result.get("fill_percentage", 0.0))
        detector = result.get("detector", "yolo")
        if low_confidence:
            logger.warning(
                "process_capture: capture %s produced 0 detections above conf=%.2f and "
                "the analyzer did not assess it; flagging the report low_confidence "
                "(vacuous fill=%.2f) rather than trusting it as a full shelf. image=%s",
                capture_id,
                conf,
                fill_percentage,
                image_path,
            )

        # 2. Persist each detected empty-space box
        for det in detections:
            db.add(
                Detection(
                    capture_id=capture.id,
                    class_=det.get("class_name", "empty"),
                    bbox=det.get("bbox"),
                    confidence=float(det.get("confidence", 0.0)),
                )
            )

        # 3. Previous fill % for the SAME camera (trend context for the LLM).
        #    "Previous" = the most recent capture created BEFORE this one that already
        #    has a report. Using id (< current, monotonic with creation) rather than
        #    captured_at avoids same-timestamp ties and never mistakes a later capture
        #    for the previous one if pipelines complete out of order.
        #    Low-confidence reports (zero detections, vacuous 1.0 fill) are skipped: their
        #    number is not a measurement, so they are not usable as a trend baseline.
        prev = (
            db.query(ShelfReport)
            .join(Capture, ShelfReport.capture_id == Capture.id)
            .filter(
                Capture.camera_id == capture.camera_id,
                Capture.id < capture.id,
                ShelfReport.low_confidence.is_(False),
            )
            .order_by(Capture.id.desc())
            .first()
        )
        previous_fill = prev.fill_percentage if prev else None

        # 4. LLM report (guaranteed-JSON tool use; rule-based fallback lives inside)
        report = generate_shelf_report(
            {
                "fill_percentage": fill_percentage,
                "aisle_name": aisle,
                "empty_region_count": len(detections),
                "detection_count": detection_count,
                "capture_id": capture_id,
                # True when the analyzer quality-gated the image and produced a real
                # measurement (possibly zero empty regions = a genuinely full shelf).
                # Lets the report layer distinguish that from a legacy unassessed result.
                "assessed": assessed,
                "detector": detector,
            },
            previous_fill,
        )

        # 5. Persist the shelf report
        shelf_report = ShelfReport(
            capture_id=capture.id,
            fill_percentage=fill_percentage,
            low_confidence=low_confidence,
            empty_regions_json=empty_regions,
            urgency=report.urgency,
            llm_summary=report.summary,
            recommended_action=report.recommended_action,
        )
        db.add(shelf_report)
        db.flush()  # obtain shelf_report.id for the alert FK

        # 6. High urgency -> Slack alert + alert email + alert row. Both notifiers never
        #    raise: an unconfigured webhook/relay or an outage returns False/[] so the
        #    alert row is still recorded (sent_to_slack/sent_to_email false) and the
        #    pipeline completes normally. The email goes to the login address of every
        #    account belonging to the store that owns this camera — i.e. to the people
        #    who can actually act on the alert.
        #    Low-confidence reports are excluded from alerting even if something upstream
        #    called them "high": an assessment the detector could not make must never page
        #    anyone. (Today the unassessed path yields a threshold urgency from the vacuous
        #    1.0 fill — always "low" — so this guard is defence in depth against future
        #    paths, not dead code guarding a live one.)
        if report.urgency == "high" and not low_confidence:
            sent = slack_service.notify_high_urgency(
                aisle=aisle,
                fill_percentage=fill_percentage,
                summary=report.summary,
                recommended_action=report.recommended_action,
            )

            # Same alert, second channel: email the login address of every account in
            # the store that owns this camera. Resolving recipients from the ``users``
            # table at send time is what makes delivery automatic: whoever signs up (or
            # logs in) with an address receives alerts there with no per-account setup.
            # A store with no user rows (or no transport configured) simply yields [] —
            # the Slack delivery above still stands.
            recipient_emails: list[str] = []
            store_name = None
            if camera is not None:
                store = camera.store
                store_name = store.name if store is not None else None
                recipient_emails = [
                    email
                    for (email,) in db.query(User.email)
                    .filter(User.store_id == camera.store_id)
                    .all()
                ]

            emailed = email_service.notify_high_urgency(
                to_emails=email_service.alert_recipients(recipient_emails),
                store_name=store_name,
                aisle=aisle,
                fill_percentage=fill_percentage,
                summary=report.summary,
                recommended_action=report.recommended_action,
            )

            db.add(
                Alert(
                    shelf_report_id=shelf_report.id,
                    sent_to_slack=bool(sent),
                    sent_to_email=bool(emailed),
                    email_recipients=", ".join(emailed) or None,
                    sent_at=datetime.now(timezone.utc) if sent else None,
                )
            )

        capture.status = "ready"
        db.commit()
        logger.info(
            "process_capture: capture %s done (fill=%.1f%%, low_confidence=%s, urgency=%s, "
            "detections=%d, detector=%s)",
            capture_id,
            fill_percentage * 100,
            low_confidence,
            report.urgency,
            len(detections),
            detector,
        )
    except Exception as exc:
        db.rollback()
        logger.exception("process_capture: pipeline failed for capture %s", capture_id)
        # Flag the capture as failed so GET /captures/{id}/report reports
        # status="failed" with a message instead of hanging at "processing".
        _mark_failed(db, capture_id, exc)
    finally:
        db.close()


def _mark_failed(db, capture_id: int, exc: Exception) -> None:
    """Best-effort: persist status='failed' + the error message on the capture.

    Runs after a rollback, so it re-fetches the capture on the now-clean session.
    Any failure to record the state is logged and swallowed — the original pipeline
    error has already been logged above.
    """
    try:
        capture = db.query(Capture).filter(Capture.id == capture_id).first()
        if capture is not None:
            capture.status = "failed"
            capture.error_message = (str(exc) or exc.__class__.__name__)[:1000]
            db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "process_capture: could not persist failure state for capture %s", capture_id
        )
