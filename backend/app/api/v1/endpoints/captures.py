"""
backend/app/api/v1/endpoints/captures.py

Capture ingestion + reporting.

POST /captures/upload        Accept a shelf image, persist the capture, and run the
                             analysis pipeline in the background. Returns 202 immediately.
GET  /captures/{id}/report   Fetch the full report once the pipeline has finished
                             (status "processing" while it runs, "completed" on
                             success, or "failed" with an error message on error).

This is the canonical capture-centric flow, matching the captures/detections/
shelf_reports schema. It supersedes ml/scripts/detect.py's never-built
/shelves/{id}/detect route.
"""

from __future__ import annotations

import os
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy.orm import Session

from app.core.auth_deps import _assert_camera_belongs_to_user, get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.detection import Detection
from app.models.shelf_report import ShelfReport
from app.schemas.capture import CaptureReportResponse, CaptureUploadResponse
from app.schemas.detection import DetectionResponse
from app.schemas.llm import ShelfReportOutput
from app.services import analyzer_service
from app.services.capture_pipeline import process_capture

router = APIRouter()

_ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


@router.post(
    "/upload",
    response_model=CaptureUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a shelf image and start background analysis",
)
async def upload_capture(
    background_tasks: BackgroundTasks,
    camera_id: int = Form(...),
    file: UploadFile = File(...),
    # Defaults live in one place (analyzer_service) so the HTTP layer, the background
    # pipeline and the ML pipeline can never drift apart again. Passing `conf` through
    # overrides ultralytics' own implicit 0.25, so there is a single filter point.
    conf: float = Form(analyzer_service.DEFAULT_CONF_THRESHOLD),
    iou: float = Form(analyzer_service.DEFAULT_IOU_THRESHOLD),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaptureUploadResponse:
    # Validate the upload is an image
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Uploaded file must be an image, got content-type '{file.content_type}'.",
        )

    # Never trust the client's camera_id — the caller could lie about which
    # camera they meant. Verify the camera actually belongs to this user's store.
    camera = _assert_camera_belongs_to_user(camera_id, db, current_user)

    contents = await file.read()
    if not contents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded file is empty.",
        )

    # Persist the image to disk under a unique name
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in _ALLOWED_EXT:
        ext = ".jpg"
    filename = f"{uuid.uuid4().hex}{ext}"
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    disk_path = os.path.join(settings.UPLOAD_DIR, filename)
    with open(disk_path, "wb") as f:
        f.write(contents)

    image_url = f"/uploads/{filename}"

    # Create the capture row (committed so the id is stable for the background task)
    capture = Capture(camera_id=camera_id, image_url=image_url)
    db.add(capture)
    db.commit()
    db.refresh(capture)

    # Run the heavy pipeline off the request path (threadpool via BackgroundTasks)
    background_tasks.add_task(process_capture, capture.id, conf, iou)

    return CaptureUploadResponse(
        capture_id=capture.id,
        status="processing",
        image_url=capture.image_url,
        captured_at=capture.captured_at,
        report_url=f"{settings.API_V1_STR}/captures/{capture.id}/report",
    )


@router.get(
    "/{capture_id}/report",
    response_model=CaptureReportResponse,
    summary="Get the analysis report for a capture",
)
def get_capture_report(
    capture_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CaptureReportResponse:
    capture = db.query(Capture).filter(Capture.id == capture_id).first()
    if capture is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Capture {capture_id} not found.",
        )
    # The capture's camera must belong to this user's store — don't let them
    # read another store's report by guessing a capture id.
    _assert_camera_belongs_to_user(capture.camera_id, db, current_user)

    # Pipeline errored — surface the failure instead of hanging at "processing".
    if capture.status == "failed":
        return CaptureReportResponse(
            capture_id=capture_id,
            status="failed",
            image_url=capture.image_url,
            captured_at=capture.captured_at,
            error_message=capture.error_message,
        )

    report = (
        db.query(ShelfReport)
        .filter(ShelfReport.capture_id == capture_id)
        .order_by(ShelfReport.created_at.desc())
        .first()
    )
    if report is None:
        # Pipeline still running (status "processing")
        return CaptureReportResponse(
            capture_id=capture_id,
            status="processing",
            image_url=capture.image_url,
            captured_at=capture.captured_at,
        )

    detections = db.query(Detection).filter(Detection.capture_id == capture_id).all()

    # Previous fill for the same camera (for display / trend): the most recent
    # capture created BEFORE this one (id <), matching the pipeline's definition.
    # Low-confidence reports (zero detections, vacuous 1.0 fill) are skipped here too —
    # their number is not a measurement, so they are not a usable trend baseline.
    prev = (
        db.query(ShelfReport)
        .join(Capture, ShelfReport.capture_id == Capture.id)
        .filter(
            Capture.camera_id == capture.camera_id,
            Capture.id < capture_id,
            ShelfReport.low_confidence.is_(False),
        )
        .order_by(Capture.id.desc())
        .first()
    )

    return CaptureReportResponse(
        capture_id=capture_id,
        status="completed",
        image_url=capture.image_url,
        captured_at=capture.captured_at,
        fill_percentage=report.fill_percentage,
        low_confidence=bool(report.low_confidence),
        empty_region_count=len(detections),
        previous_fill=prev.fill_percentage if prev else None,
        report=ShelfReportOutput(
            summary=report.llm_summary or "",
            urgency=report.urgency,
            recommended_action=report.recommended_action or "",
        ),
        detections=[DetectionResponse.model_validate(d) for d in detections],
    )
