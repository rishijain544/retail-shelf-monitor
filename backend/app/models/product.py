from datetime import datetime, timezone

from sqlalchemy import Column, Integer, String, Float, DateTime, func
from app.core.database import Base

class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    upc = Column(String, unique=True, index=True, nullable=False)
    price = Column(Float, nullable=False)
    expected_quantity = Column(Integer, default=0, nullable=False)
    current_quantity = Column(Integer, default=0, nullable=False)
    # Python-side defaults/onupdate so the ORM never emits dialect-specific SQL:
    # SQLite cannot evaluate Postgres' now().
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at = Column(DateTime(timezone=True), onupdate=lambda: datetime.now(timezone.utc))

    def __repr__(self):
        return f"<Product(name='{self.name}', upc='{self.upc}', quantity={self.current_quantity}/{self.expected_quantity})>"
