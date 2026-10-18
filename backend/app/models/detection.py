from sqlalchemy import Column, Integer, String, Float, ForeignKey, JSON
from sqlalchemy.orm import relationship
from app.core.database import Base

class Detection(Base):
    __tablename__ = "detections"

    id = Column(Integer, primary_key=True, index=True)
    capture_id = Column(Integer, ForeignKey("captures.id", ondelete="CASCADE"), nullable=False)
    
    # Map Python 'class_' to database column 'class' to bypass reserved keyword conflict
    class_ = Column("class", String, nullable=False)
    
    bbox = Column(JSON, nullable=False)  # Stores coordinates, e.g., [xmin, ymin, xmax, ymax]
    confidence = Column(Float, nullable=False)

    # Relationships
    capture = relationship("Capture", back_populates="detections")

    def __repr__(self):
        return f"<Detection(id={self.id}, capture_id={self.capture_id}, class='{self.class_}', confidence={self.confidence})>"
