"""
backend/app/api/v1/endpoints/alerts.py

Alert rows + delivery connectivity check. One alert is created per high-urgency shelf
report; the same alert goes to Slack (webhook) and to the store's login email (SMTP).

GET  /alerts        Paginated list of alerts (newest first).
POST /alerts/test   Send a diagnostic message to Slack and to the caller's login email.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.core.auth_deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.models.alert import Alert
from app.models.camera import Camera
from app.models.capture import Capture
from app.models.shelf_report import ShelfReport
from app.models.store import Store
from app.models.user import User
from app.schemas.alert import AlertResponse
from app.schemas.pagination import Page
from app.services import email_service, email_setup, email_settings, slack_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _with_context(alert: Alert) -> AlertResponse:
    """
    Serialize an alert and attach the context its own row lacks.

    An alerts row references only its shelf report; the urgency and summary live
    on that report, and the store/aisle two hops further on (report -> capture ->
    camera -> store). An alert feed needs all of it per row, so resolve it here
    instead of having the client fan out a request per alert. Every hop is
    optional — any upstream row may be missing.
    """
    payload = AlertResponse.model_validate(alert)

    report = alert.shelf_report
    capture = report.capture if report is not None else None
    camera = capture.camera if capture is not None else None
    store = camera.store if camera is not None else None

    if report is not None:
        payload.urgency = report.urgency
        payload.llm_summary = report.llm_summary
        payload.report_created_at = report.created_at
    if camera is not None:
        payload.camera_id = camera.id
        payload.camera_name = camera.name
        payload.aisle = camera.aisle
        payload.store_id = camera.store_id
    if store is not None:
        payload.store_name = store.name

    return payload


# Eager-load shelf_report -> capture -> camera -> store so _with_context() does
# not emit a cascade of queries per alert (N+1) when serializing a page.
_CONTEXT_LOAD = (
    joinedload(Alert.shelf_report)
    .joinedload(ShelfReport.capture)
    .joinedload(Capture.camera)
    .joinedload(Camera.store)
)


@router.get(
    "",
    response_model=Page[AlertResponse],
    summary="List alerts (paginated)",
)
def list_alerts(
    limit: int = Query(50, ge=1, le=200, description="Max alerts per page (1–200)."),
    offset: int = Query(0, ge=0, description="Alerts to skip before this page."),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Page[AlertResponse]:
    """
    Return a page of alerts for the current user's store, most recent first.

    Each alert corresponds to a high-urgency shelf report; ``sent_to_slack``/
    ``sent_to_email`` record whether the Slack message and the login-email copy were
    delivered, ``email_recipients`` who received the mail, and ``sent_at`` when Slack
    accepted its message.
    Each row also carries the report's ``urgency``/``llm_summary`` and the store and
    aisle it came from, resolved through the capture's camera. The scope is always
    the current user's store — a caller must never see alerts belonging to another
    store.
    """
    # Build the scoped query up front, so total and rows are consistent.
    query = (
        db.query(Alert)
        .join(ShelfReport, Alert.shelf_report_id == ShelfReport.id)
        .join(Capture, ShelfReport.capture_id == Capture.id)
        .join(Camera, Capture.camera_id == Camera.id)
        .filter(Camera.store_id == current_user.store_id)
    )
    total = query.count()
    rows = (
        query.options(_CONTEXT_LOAD)
        .order_by(Alert.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return Page[AlertResponse](
        items=[_with_context(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


class EmailConfigRequest(BaseModel):
    """A provider API key, optionally with the From: address and alert recipient."""

    api_key: str = Field(..., min_length=8, description="Provider API key (e.g. re_…)")
    from_email: Optional[str] = Field(
        None, description='Optional sender, e.g. "ShelfMonitor <alerts@yourdomain.com>"'
    )
    alert_email_to: Optional[str] = Field(
        None,
        description=(
            "Comma-separated recipient addresses for ALL alert emails. Required for "
            "Resend free tier, which only delivers to the Resend account's own address."
        ),
    )


class EmailConfigResponse(BaseModel):
    configured: bool
    transport: str
    from_address: str
    last_error: Optional[str] = None
    key: Optional[str] = None  # masked fingerprint, never the key itself
    detail: Optional[str] = None
    email_delivered_to: list[str] = []
    alert_email_to: Optional[str] = None
    # SMTP relay details, populated when the active transport is SMTP.
    smtp_host: Optional[str] = None
    smtp_user: Optional[str] = None


def _email_config_state() -> EmailConfigResponse:
    return EmailConfigResponse(
        configured=email_service.is_configured(),
        transport=email_service.transport(),
        from_address=settings.email_from_address,
        last_error=email_service.config_error() or email_service.last_error(),
        alert_email_to=settings.ALERT_EMAIL_TO,
        smtp_host=settings.SMTP_HOST,
        smtp_user=settings.SMTP_USER,
    )


def _persist_email_settings(saver, db: Session, **values) -> None:
    """Store verified credentials, translating a DB failure into a clear 500.

    The credentials are already live in this process (``email_setup`` applied them
    before the test send), so the request is not a no-op — but without the row they
    would be lost at the next restart. Saying that explicitly beats a generic
    "Internal server error".
    """
    try:
        saver(db, **values)
    except SQLAlchemyError:
        db.rollback()
        logger.exception("alerts: verified email credentials could not be saved")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=(
                "The mail credentials were verified and are active, but saving them to "
                "the database failed, so they will be lost on restart. Check the "
                "database connection and save again."
            ),
        ) from None


class SmtpConfigRequest(BaseModel):
    """SMTP relay credentials — a Gmail App Password delivers to ANY login email.

    Saved once for the whole installation, not per account: an SMTP password is a
    *sending* credential, so this one relay reaches every signed-in account's address.
    A Resend free-tier key cannot — it only delivers to the Resend account's own address
    — which is why SMTP is the transport that makes automatic per-login-email alerting
    work without a verified domain or a paid plan.
    """

    host: str = Field(..., min_length=1, description="SMTP host, e.g. smtp.gmail.com")
    port: int = Field(587, ge=1, le=65535, description="SMTP port (587 STARTTLS, 465 SSL)")
    user: str = Field(..., min_length=1, description="SMTP login (your Gmail address)")
    password: str = Field(..., min_length=1, description="SMTP password / Gmail App Password")
    smtp_from: Optional[str] = Field(
        None, description="From: address; defaults to the SMTP login when blank"
    )
    use_tls: bool = Field(True, description="STARTTLS (port 587)")
    use_ssl: bool = Field(False, description="Implicit TLS (port 465)")


@router.post(
    "/smtp-config",
    response_model=EmailConfigResponse,
    summary="Verify, save and test SMTP relay credentials",
)
def save_smtp_config(
    payload: SmtpConfigRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailConfigResponse:
    """Install an SMTP relay so alerts reach every account's login email.

    A Gmail App Password (or any SMTP relay) can deliver to any address, unlike a
    Resend free-tier key. The credentials are proven by sending a test message to the
    caller's own login address *before* anything is stored, so a bad password fails
    loudly here and leaves any working configuration in place.

    The verified credentials are saved in the database (``email_settings``), not in
    ``backend/.env``: a deployed container's filesystem is read-only or ephemeral, so
    env-file persistence would silently lose the relay on the next redeploy.
    """
    store = db.query(Store).filter(Store.id == current_user.store_id).first()
    try:
        applied = email_setup.install_smtp(
            payload.host,
            payload.port,
            payload.user,
            payload.password,
            payload.smtp_from,
            test_recipient=current_user.email,
            store_name=store.name if store is not None else None,
            use_tls=payload.use_tls,
            use_ssl=payload.use_ssl,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    _persist_email_settings(
        email_settings.save_smtp,
        db,
        host=payload.host.strip(),
        port=payload.port,
        user=payload.user.strip(),
        password=payload.password.strip(),
        smtp_from=(payload.smtp_from or "").strip() or payload.user.strip(),
        use_tls=payload.use_tls,
        use_ssl=payload.use_ssl,
    )

    state = _email_config_state()
    state.detail = "SMTP relay verified, saved and test email delivered"
    state.email_delivered_to = applied["delivered"]
    return state


@router.get(
    "/email-config",
    response_model=EmailConfigResponse,
    summary="Report the alert-email configuration",
)
def get_email_config(current_user: User = Depends(get_current_user)) -> EmailConfigResponse:
    """Whether alert email can send, over which transport, and why it last failed."""
    return _email_config_state()


@router.post(
    "/email-config",
    response_model=EmailConfigResponse,
    summary="Verify, save and test an email API key",
)
def save_email_config(
    payload: EmailConfigRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> EmailConfigResponse:
    """Install a mail-provider API key without editing files or restarting.

    The key is proven by sending a test message to the caller's own login address *before*
    anything is stored. A bad key therefore fails here, loudly, and leaves any previously
    working configuration in place — instead of silently turning every future alert into
    "Not sent".

    Verified credentials go to the ``email_settings`` row so they survive a redeploy;
    writing ``backend/.env`` only worked on a machine with a writable disk.
    """
    store = db.query(Store).filter(Store.id == current_user.store_id).first()
    # The test recipient is the alert_email_to address when provided (required for
    # Resend free tier, which only delivers to the account owner's address), otherwise
    # the caller's own login email.
    test_recipient = current_user.email
    if payload.alert_email_to:
        first = payload.alert_email_to.split(",")[0].strip()
        if first:
            test_recipient = first
    try:
        applied = email_setup.install(
            payload.api_key,
            payload.from_email,
            test_recipient=test_recipient,
            store_name=store.name if store is not None else None,
            alert_email_to=payload.alert_email_to,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    _persist_email_settings(
        email_settings.save_api,
        db,
        api_key=email_service.normalize_api_key(payload.api_key),
        from_email=(payload.from_email or "").strip() or None,
        alert_email_to=(payload.alert_email_to or "").strip() or None,
    )

    state = _email_config_state()
    state.key = applied["key"]
    state.detail = "key verified, saved and test email delivered"
    state.email_delivered_to = applied["delivered"]
    return state


@router.post(
    "/test",
    summary="Send a test alert to Slack and to your login email",
    status_code=status.HTTP_200_OK,
)
def send_test_alert(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    """
    Prove that alerting is wired up without waiting for a real high-urgency capture.

    The test message goes through exactly the same channels a real alert uses: the
    Slack webhook (`SLACK_WEBHOOK_URL`) and — addressed to the caller's own login
    email — the email transport (`RESEND_API_KEY`, or `SMTP_HOST`/`SMTP_USER`/…).
    A channel that is not configured is reported as ``not_configured`` instead of
    failing the whole call.

    Responses:

    * **200** — delivered on at least one configured channel.
    * **503** — neither Slack nor SMTP is configured.
    * **502** — every configured channel failed to deliver.
    """
    slack_configured = bool(settings.SLACK_WEBHOOK_URL)
    email_configured = email_service.is_configured()

    if not slack_configured and not email_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No alert channel is configured: set SLACK_WEBHOOK_URL (environment), "
                "and/or install an email transport from Alerts -> Email setup "
                "(RESEND_API_KEY or SMTP_HOST + SMTP_FROM env vars also work)."
            ),
        )

    slack_state = "not_configured"
    if slack_configured:
        slack_state = "sent" if slack_service.send_test_message() else "failed"

    email_state = "not_configured"
    recipients: list[str] = []
    if email_configured:
        store = db.query(Store).filter(Store.id == current_user.store_id).first()
        # The test goes to the caller's own login address — exactly the address real
        # alerts reach for this account, with no extra recipient to configure.
        recipients = email_service.send_test_message(
            email_service.alert_recipients([current_user.email]),
            store_name=store.name if store is not None else None,
        )
        email_state = "sent" if recipients else "failed"

    delivered = [
        label
        for label, state in (("Slack", slack_state), ("email", email_state))
        if state == "sent"
    ]
    if not delivered:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                "Alert channels are configured but delivery failed "
                f"(slack={slack_state}, email={email_state}). Check the backend logs."
            ),
        )

    parts = []
    if slack_state == "sent":
        parts.append("Slack")
    if email_state == "sent":
        parts.append(f"email to {', '.join(recipients)}")

    return {
        "status": "sent",
        "detail": f"Test alert delivered via {' and '.join(parts)}.",
        "slack": slack_state,
        "email": email_state,
        "email_recipients": recipients,
    }
