"""
backend/app/api/v1/endpoints/reports.py

Read-only access to the shelf reports produced by the capture pipeline.

GET /reports        Paginated, filterable list of shelf reports (newest first).
GET /reports/export CSV export of matching shelf reports (unpaginated, complete).
GET /reports/{id}   A single shelf report by id.
"""
from __future__ import annotations

from csv import writer as csv_writer
from datetime import datetime
from io import StringIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload

from app.core.auth_deps import get_current_user
from app.core.database import get_db
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.shelf_report import ShelfReport
from app.schemas.pagination import Page
from app.schemas.shelf_report import ShelfReportResponse

router = APIRouter()


def _with_context(report: ShelfReport) -> ShelfReportResponse:
    """
    Serialize a report and attach the capture context the row itself lacks.

    A shelf_reports row references only its capture; the store and aisle live on
    the camera behind that capture. Callers listing reports need those to label
    each one, so resolve them here rather than making the client fan out a
    request per report. Each hop is optional — a capture, camera or store row can
    be missing (cascades aside, the columns are only FK-constrained one level).
    """
    payload = ShelfReportResponse.model_validate(report)

    capture = report.capture
    camera = capture.camera if capture is not None else None
    store = camera.store if camera is not None else None

    if camera is not None:
        payload.camera_id = camera.id
        payload.camera_name = camera.name
        payload.aisle = camera.aisle
        payload.store_id = camera.store_id
    if store is not None:
        payload.store_name = store.name

    return payload


# Eager-load capture -> camera -> store so _with_context() does not emit a
# query per report (N+1) when serializing a page.
_CONTEXT_LOAD = (
    joinedload(ShelfReport.capture)
    .joinedload(Capture.camera)
    .joinedload(Camera.store)
)


def _apply_report_filters(query, store_id, aisle, urgency, start_date, end_date):
    """Apply the same filters used by GET /reports to a query."""
    if store_id is not None or aisle is not None:
        query = query.join(Capture, ShelfReport.capture_id == Capture.id).join(
            Camera, Capture.camera_id == Camera.id
        )
        if store_id is not None:
            query = query.filter(Camera.store_id == store_id)
        if aisle is not None:
            query = query.filter(Camera.aisle == aisle)

    if urgency is not None:
        query = query.filter(ShelfReport.urgency == urgency)
    if start_date is not None:
        query = query.filter(ShelfReport.created_at >= start_date)
    if end_date is not None:
        query = query.filter(ShelfReport.created_at <= end_date)

    return query


@router.get(
    "",
    response_model=Page[ShelfReportResponse],
    summary="List shelf reports (filterable, paginated)",
)
def list_reports(
    aisle: Optional[str] = Query(
        None, description="Only reports whose capture came from this aisle (exact match)."
    ),
    urgency: Optional[str] = Query(
        None, description='Only reports with this urgency ("low", "medium", or "high").'
    ),
    start_date: Optional[datetime] = Query(
        None, description="Only reports created at or after this ISO-8601 timestamp."
    ),
    end_date: Optional[datetime] = Query(
        None, description="Only reports created at or before this ISO-8601 timestamp."
    ),
    limit: int = Query(50, ge=1, le=200, description="Max reports per page (1–200)."),
    offset: int = Query(0, ge=0, description="Reports to skip before this page."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[ShelfReportResponse]:
    """
    Return a page of shelf reports belonging to the current user's store,
    most recent first.

    All filters are optional and combine with AND. ``aisle`` filters on the camera
    that produced each capture; ``urgency`` and the ``start_date``/``end_date``
    range filter on the report itself (its ``created_at``). The scope is always the
    current user's store — the client must never supply a ``store_id`` (it would be
    ignored even if accepted).
    """
    query = _apply_report_filters(
        db.query(ShelfReport),
        store_id=current_user.store_id,
        aisle=aisle,
        urgency=urgency,
        start_date=start_date,
        end_date=end_date,
    )

    total = query.count()
    rows = (
        query.options(_CONTEXT_LOAD)
        .order_by(ShelfReport.created_at.desc(), ShelfReport.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return Page[ShelfReportResponse](
        items=[_with_context(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/export",
    summary="Export shelf reports as CSV",
    response_class=Response,
)
def export_reports(
    aisle: Optional[str] = Query(
        None, description="Only reports whose capture came from this aisle (exact match)."
    ),
    urgency: Optional[str] = Query(
        None, description='Only reports with this urgency ("low", "medium", or "high").'
    ),
    start_date: Optional[datetime] = Query(
        None, description="Only reports created at or after this ISO-8601 timestamp."
    ),
    end_date: Optional[datetime] = Query(
        None, description="Only reports created at or before this ISO-8601 timestamp."
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """
    Return every shelf report for the current user's store matching the given
    filters as a CSV file.

    Unlike GET /reports, this endpoint is unpaginated and returns the complete
    result set — suitable for offline analysis or ops tooling. The CSV includes
    both the report's own fields and the capture context (store, aisle, camera)
    resolved through the capture's camera.

    The response carries a ``Content-Disposition`` header so browsers download it
    as a file named ``shelf-reports-<start>-to-<end>.csv``.
    """
    query = _apply_report_filters(
        db.query(ShelfReport),
        store_id=current_user.store_id,
        aisle=aisle,
        urgency=urgency,
        start_date=start_date,
        end_date=end_date,
    )
    rows = (
        query.options(_CONTEXT_LOAD)
        .order_by(ShelfReport.created_at.desc(), ShelfReport.id.desc())
        .all()
    )

    output = StringIO()
    w = csv_writer(output)
    w.writerow([
        "store_name", "aisle", "camera_name", "fill_percentage", "low_confidence",
        "urgency", "llm_summary", "recommended_action", "created_at",
    ])
    for r in rows:
        w.writerow([
            r.capture.camera.store.name if (r.capture and r.capture.camera and r.capture.camera.store) else "",
            r.capture.camera.aisle if (r.capture and r.capture.camera) else "",
            r.capture.camera.name if (r.capture and r.capture.camera) else "",
            # Vacuous 1.0 on a zero-detection capture — consumers must read it together
            # with the low_confidence column, which says whether it is a real measurement.
            r.fill_percentage,
            "true" if r.low_confidence else "false",
            r.urgency,
            r.llm_summary or "",
            r.recommended_action or "",
            r.created_at.isoformat() if r.created_at else "",
        ])

    filename = _export_filename(start_date, end_date)
    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename={filename}",
        },
    )


def _export_filename(start_date: Optional[datetime], end_date: Optional[datetime]) -> str:
    """Build a sensible download filename from the date range (or today if none)."""
    def _fmt(dt: Optional[datetime]) -> str:
        if dt is None:
            return datetime.now().strftime("%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    return f"shelf-reports-{_fmt(start_date)}-to-{_fmt(end_date)}.csv"


@router.get(
    "/{report_id}",
    response_model=ShelfReportResponse,
    summary="Get a single shelf report by id (authenticated)",
)
def get_report(
    report_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ShelfReportResponse:
    """Return the shelf report with this id, or 404 if none exists in the current
    user's store."""
    report = (
        db.query(ShelfReport)
        .options(_CONTEXT_LOAD)
        .join(Capture, ShelfReport.capture_id == Capture.id)
        .join(Camera, Capture.camera_id == Camera.id)
        .filter(
            ShelfReport.id == report_id,
            Camera.store_id == current_user.store_id,
        )
        .first()
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Shelf report {report_id} not found.",
        )
    return _with_context(report)
