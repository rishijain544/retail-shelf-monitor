from sqlalchemy import Column, Integer, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import relationship
from app.core.database import Base

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    shelf_report_id = Column(Integer, ForeignKey("shelf_reports.id", ondelete="CASCADE"), nullable=False)
    sent_to_slack = Column(Boolean, default=False, nullable=False)
    # The same alert is also mailed to the store's login address(es). False when SMTP
    # is unconfigured, the store has no users, or delivery failed — the row is still
    # recorded so the dashboard can show what happened.
    sent_to_email = Column(Boolean, default=False, nullable=False)
    # Comma-joined addresses that were actually delivered to (NULL when none were),
    # so an operator can tell who received the alert without reading SMTP logs.
    email_recipients = Column(String(1000), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    shelf_report = relationship("ShelfReport", back_populates="alerts")

    def __repr__(self):
        return (
            f"<Alert(id={self.id}, shelf_report_id={self.shelf_report_id}, "
            f"sent_to_slack={self.sent_to_slack}, sent_to_email={self.sent_to_email})>"
        )
