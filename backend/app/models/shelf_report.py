from datetime import datetime, timezone

from sqlalchemy import Column, Integer, Float, String, DateTime, ForeignKey, JSON, Boolean, false, func
from sqlalchemy.orm import relationship
from app.core.database import Base

class ShelfReport(Base):
    __tablename__ = "shelf_reports"

    id = Column(Integer, primary_key=True, index=True)
    capture_id = Column(Integer, ForeignKey("captures.id", ondelete="CASCADE"), nullable=False)
    fill_percentage = Column(Float, nullable=False)
    # True when the detector returned zero detections. The numeric fill is then the
    # analyzer's vacuous 1.0 ("no empty space found"), NOT a confident measurement — a
    # boolean flag is used instead of a NULL fill so every consumer that assumes a clean
    # float (trends chart, sorting/filters, CSV export) keeps working. Such rows must be
    # presented as "unable to assess", never used as a trend baseline, and never alerted on.
    low_confidence = Column(Boolean, nullable=False, default=False, server_default=false())
    empty_regions_json = Column(JSON, nullable=True)  # Stores list of empty coordinates/bounding boxes
    urgency = Column(String, nullable=False)  # e.g., "LOW", "MEDIUM", "HIGH", "CRITICAL"
    llm_summary = Column(String, nullable=True)
    recommended_action = Column(String, nullable=True)  # concrete action from the LLM report
    # Python-side default so the ORM always supplies a value: the DB-level default is
    # dialect-specific (SQLite cannot evaluate Postgres' now()), and a table created
    # by an older migration may still carry the broken one.
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    capture = relationship("Capture", back_populates="shelf_reports")
    alerts = relationship("Alert", back_populates="shelf_report", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<ShelfReport(id={self.id}, capture_id={self.capture_id}, fill={self.fill_percentage}%, urgency='{self.urgency}')>"
