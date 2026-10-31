from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, false, func
from sqlalchemy.orm import relationship
from app.core.database import Base

class User(Base):
    """
    A dashboard login. One account per store for now, so `store_id` is a plain
    foreign key rather than a many-to-many; it can be relaxed later without
    changing this table's shape.

    `email` is stored lower-cased (see app/schemas/auth.py) so the unique index
    actually prevents duplicates such as "Owner@Shop.com" vs "owner@shop.com".

    Account security state lives here rather than in a side table: every login has to
    read all of it in one query, and a lockout that could be stale against an MFA
    flag is a bypass waiting to happen.

    `mfa_exempt` skips the authenticator step for accounts that cannot hold a second
    factor — only the public demo account today. It is never writable through the API
    (no schema exposes it); scripts/seed.py and scripts/reset_mfa.py are the only
    writers.
    """
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, nullable=False, unique=True, index=True)
    hashed_password = Column(String, nullable=False)
    store_id = Column(
        Integer,
        ForeignKey("stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ─── Email verification ──────────────────────────────────────────────────
    # False until the signup code is confirmed: logging in with an unverified
    # address is a 403, so a typo'd or someone else's address cannot become a
    # working account. Existing rows are grandfathered true by the migration.
    email_verified = Column(
        Boolean, nullable=False, default=False, server_default=false()
    )

    # ─── MFA ─────────────────────────────────────────────────────────────────
    # True once an authenticator has been confirmed. The secret stays stored while
    # enabled; it is encrypted (see security.encrypt_mfa_secret), never returned.
    mfa_enabled = Column(Boolean, nullable=False, default=False, server_default=false())
    mfa_secret_encrypted = Column(String, nullable=True)
    # The last accepted TOTP time step. A code's step must be strictly greater, which
    # is what stops a shoulder-surfed code being replayed inside its 30s window.
    mfa_last_used_step = Column(Integer, nullable=True)
    # Password-only accounts (the shared demo login). Not settable through the API.
    mfa_exempt = Column(Boolean, nullable=False, default=False, server_default=false())

    # ─── Lockout counters ────────────────────────────────────────────────────
    failed_login_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    login_locked_until = Column(DateTime(timezone=True), nullable=True)
    failed_mfa_attempts = Column(Integer, nullable=False, default=0, server_default="0")
    mfa_locked_until = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    store = relationship("Store", back_populates="users")

    def __repr__(self):
        # Never include the MFA secret or password hash in a repr: it reaches logs.
        return f"<User(id={self.id}, email='{self.email}', store_id={self.store_id})>"
