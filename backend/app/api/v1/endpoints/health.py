"""
backend/app/api/v1/endpoints/health.py

Liveness probe: the API itself plus the database it depends on.

(Redis was removed from the stack entirely — the queue broker was never used
outside Docker deployments the project no longer targets — so there is no
cache/broker dependency to check here anymore.)
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.services import email_service

router = APIRouter()


def _email_alert_status() -> str:
    """Alert-email delivery state, including *why* the last attempt failed.

    "Configured" is not the same as "working": a key can be present and still be
    rejected by the provider. Reporting the last failure here means the dashboard
    says "Not working — the provider rejected the API key" instead of showing a
    confident green badge while every alert is silently not emailed.
    """
    config_problem = email_service.config_error()
    if not email_service.is_configured():
        return f"unhealthy: {config_problem}" if config_problem else "not_configured"

    problem = config_problem or email_service.last_error()
    return f"unhealthy: {problem}" if problem else "healthy"


@router.get("/health", summary="Health check endpoint")
def health_check(db: Session = Depends(get_db)):
    """
    Check health status of the application and its database.

    An unreachable dependency is reported as an ``unhealthy: <reason>`` string
    rather than blocking or failing the request.
    """
    health_status = {
        "status": "healthy",
        "services": {
            "api": "healthy",
            "database": "unknown",
            # Alert delivery channels. "not_configured" is not a failure — it means no
            # credentials were supplied, which is the reason an alert can be raised and
            # yet no message leaves the building. Reporting it here makes that visible
            # in the dashboard instead of leaving it to silent "Not sent" rows.
            "alert_slack": "healthy" if settings.SLACK_WEBHOOK_URL else "not_configured",
            "alert_email": _email_alert_status(),
            "alert_email_transport": email_service.transport(),
        },
    }

    # Check Database connection
    try:
        db.execute(text("SELECT 1"))
        health_status["services"]["database"] = "healthy"
    except Exception as e:
        health_status["status"] = "unhealthy"
        health_status["services"]["database"] = f"unhealthy: {str(e)}"

    return health_status
