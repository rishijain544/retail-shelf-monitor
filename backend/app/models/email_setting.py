"""
backend/app/models/email_setting.py

Installation-wide email transport credentials, stored in the database.

Why the DB and not ``backend/.env``: the Email setup screen writes these at runtime,
and a deployed container's filesystem is read-only (Render) or ephemeral (Vercel).
Writing ``.env`` therefore only ever worked on a developer machine — after a redeploy
the verified key was gone and every alert silently recorded ``sent_to_email=false``.
A row here survives redeploys and restarts.

There is exactly one row (``id = 1``): this is one sending identity for the whole
installation, not per-user mail credentials. The key is never returned by the API —
``/alerts/email-config`` only reports a masked fingerprint.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, false, func, true

from app.core.database import Base


class EmailSetting(Base):
    __tablename__ = "email_settings"

    # Fixed singleton id; the service always reads/writes row 1.
    id = Column(Integer, primary_key=True)

    # Active transport: "api" (Resend-style HTTPS API) or "smtp". NULL when the row
    # only carries leftovers, in which case env-var configuration stays in charge.
    transport = Column(String(length=10), nullable=True)

    # Transport 1 — API key. Enough on its own (no SMTP account needed).
    resend_api_key = Column(String(length=255), nullable=True)
    email_from = Column(String(length=320), nullable=True)
    # Comma-separated override for every alert recipient. Needed on Resend's free
    # tier, which only delivers to the account owner's own address.
    alert_email_to = Column(String(length=1000), nullable=True)

    # Transport 2 — SMTP relay (a Gmail App Password can reach any login address).
    smtp_host = Column(String(length=255), nullable=True)
    smtp_port = Column(Integer, nullable=True)
    smtp_user = Column(String(length=320), nullable=True)
    smtp_password = Column(String(length=512), nullable=True)
    smtp_from = Column(String(length=320), nullable=True)
    smtp_use_tls = Column(Boolean, nullable=False, default=True, server_default=true())
    smtp_use_ssl = Column(Boolean, nullable=False, default=False, server_default=false())

    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )

    def __repr__(self) -> str:
        # Never include credentials in a repr: it ends up in logs on error.
        return f"<EmailSetting(id={self.id}, transport={self.transport!r})>"
