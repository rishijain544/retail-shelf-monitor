from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base

class Camera(Base):
    __tablename__ = "cameras"

    id = Column(Integer, primary_key=True, index=True)
    store_id = Column(Integer, ForeignKey("stores.id", ondelete="CASCADE"), nullable=False)
    aisle = Column(String, nullable=False)
    name = Column(String, nullable=False)

    # Relationships
    store = relationship("Store", back_populates="cameras")
    captures = relationship("Capture", back_populates="camera", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Camera(id={self.id}, name='{self.name}', aisle='{self.aisle}', store_id={self.store_id})>"
