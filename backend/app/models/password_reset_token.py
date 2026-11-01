"""
backend/app/models/password_reset_token.py

Single-use proof that someone can read an account's mailbox.

The token itself exists only in the email link. What is stored is its SHA-256 digest:
the token is 256 bits of `secrets.token_urlsafe(32)`, so an unkeyed digest is enough
(there is nothing to brute-force), and a database dump hands over no working link.
Comparisons use ``hmac.compare_digest`` anyway so a lookup cannot be narrowed by timing.

Rows are kept after use (``used_at`` set) rather than deleted: the per-account cooldown
and the audit trail of "a reset was requested from this address" both need the history.
Issuing a new token marks every earlier unused one used, so at most one link works.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.core.database import Base


def _utcnow() -> datetime:
    # Python-side default: SQLite cannot evaluate Postgres' now().
    return datetime.now(timezone.utc)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SHA-256 hex digest of the emailed token; the token itself is never stored.
    token_hash = Column(String(length=64), nullable=False, unique=True, index=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Set when the token is used or superseded; only NULL rows can still reset.
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    # Best-effort address of the requester, for support and abuse triage. Never used
    # for authorisation; the reset itself only needs the token.
    requested_ip = Column(String(length=64), nullable=True)

    user = relationship("User")

    def __repr__(self) -> str:
        # The digest is credential material; keep it out of logs.
        return f"<PasswordResetToken(id={self.id}, user_id={self.user_id})>"
