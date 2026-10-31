"""
backend/app/models/mfa_recovery_code.py

One-time backup codes for an enrolled account.

These are the only way back in after a lost phone, because there is no password-reset
flow yet. They are bcrypt-hashed (a 32-bit-entropy code is still guessable offline if
stored in clear) and marked with ``used_at`` instead of being deleted, so "this code
was already used" can be told apart from "this code never existed" in the logs.

They are shown to the user in plaintext exactly once, in the response that creates
them. Nothing in the API ever reads them back.
"""

from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.core.database import Base


class MfaRecoveryCode(Base):
    __tablename__ = "mfa_recovery_codes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # bcrypt digest (60 chars today; the column leaves room for a future scheme).
    code_hash = Column(String(length=255), nullable=False)
    used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")

    def __repr__(self) -> str:
        return f"<MfaRecoveryCode(id={self.id}, user_id={self.user_id})>"
