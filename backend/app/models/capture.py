from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, func
from sqlalchemy.orm import relationship
from app.core.database import Base

class Capture(Base):
    __tablename__ = "captures"

    id = Column(Integer, primary_key=True, index=True)
    camera_id = Column(Integer, ForeignKey("cameras.id", ondelete="CASCADE"), nullable=False)
    image_url = Column(String, nullable=False)
    # Python-side default so the ORM always supplies a value: the DB-level default
    # is dialect-specific (SQLite cannot evaluate Postgres' now()), and a table
    # created by an older migration may still carry the broken one.
    captured_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    # Pipeline lifecycle: "processing" at upload -> "ready" on success, or "failed"
    # on any pipeline error. error_message holds the failure detail so the report
    # endpoint can surface it instead of hanging at "processing" forever.
    status = Column(String, nullable=False, server_default="processing", default="processing")
    error_message = Column(String, nullable=True)

    # Relationships
    camera = relationship("Camera", back_populates="captures")
    detections = relationship("Detection", back_populates="capture", cascade="all, delete-orphan")
    shelf_reports = relationship("ShelfReport", back_populates="capture", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Capture(id={self.id}, camera_id={self.camera_id}, status='{self.status}', image_url='{self.image_url}', captured_at={self.captured_at})>"
