from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import relationship
from app.core.database import Base

class User(Base):
    """
    A dashboard login. One account per store for now, so `store_id` is a plain
    foreign key rather than a many-to-many; it can be relaxed later without
    changing this table's shape.

    `email` is stored lower-cased (see app/schemas/auth.py) so the unique index
    actually prevents duplicates such as "Owner@Shop.com" vs "owner@shop.com".
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

    # Relationships
    store = relationship("Store", back_populates="users")

    def __repr__(self):
        return f"<User(id={self.id}, email='{self.email}', store_id={self.store_id})>"
