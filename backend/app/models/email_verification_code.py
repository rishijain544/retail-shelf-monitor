"""
backend/app/models/email_verification_code.py

Short-lived, single-use codes proving the signup address is real.

The plaintext code exists only in the email (and, in local development only, the log).
What is stored is an HMAC-SHA256 digest keyed with the server's secret, compared with
``hmac.compare_digest`` — a six-digit code is far too short to survive a fast unkeyed
hash (a million candidates is milliseconds of work), so the key is what makes an
offline guess against a database dump useless.

Rows are kept after use (``consumed_at`` set) rather than deleted: the resend cooldown
and the "5 attempts then burn the code" rule both need the history.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship

from app.core.database import Base


def _utcnow() -> datetime:
    # Python-side default: SQLite cannot evaluate Postgres' now().
    return datetime.now(timezone.utc)


class EmailVerificationCode(Base):
    __tablename__ = "email_verification_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # HMAC-SHA256 hex digest of the code; never the code itself.
    code_hash = Column(String(length=64), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    # Wrong guesses so far. Reaching MAX_CODE_ATTEMPTS burns the code.
    attempts = Column(Integer, nullable=False, default=0, server_default="0")
    created_at = Column(
        DateTime(timezone=True), nullable=False, default=_utcnow, server_default=func.now()
    )
    # Set when the code is used or burned; only NULL rows can still verify.
    consumed_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")

    def __repr__(self) -> str:
        # The digest is credential material; keep it out of logs.
        return f"<EmailVerificationCode(id={self.id}, user_id={self.user_id})>"
