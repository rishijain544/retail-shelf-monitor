from sqlalchemy import Column, Integer, String
from sqlalchemy.orm import relationship
from app.core.database import Base

class Store(Base):
    __tablename__ = "stores"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    location = Column(String, nullable=True)

    # Relationships
    cameras = relationship("Camera", back_populates="store", cascade="all, delete-orphan")
    users = relationship("User", back_populates="store", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Store(id={self.id}, name='{self.name}', location='{self.location}')>"
