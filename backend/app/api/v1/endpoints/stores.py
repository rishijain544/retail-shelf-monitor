"""
backend/app/api/v1/endpoints/stores.py

Store directory — read and create.

GET  /stores        List all stores.
POST /stores        Create a new store.
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.core.auth_deps import get_current_user
from app.core.database import get_db
from app.models.store import Store
from app.schemas.store import StoreCreate, StoreResponse

router = APIRouter()


@router.get(
    "",
    response_model=List[StoreResponse],
    summary="List all stores (authenticated)",
)
def list_stores(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[StoreResponse]:
    """Return every store, ordered by id. Auth-required; no tenant scoping here
    since a store directory is read-only reference data."""
    return db.query(Store).order_by(Store.id).all()


@router.post(
    "",
    response_model=StoreResponse,
    summary="Create a new store",
    status_code=status.HTTP_201_CREATED,
)
def create_store(
    store: StoreCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> StoreResponse:
    """
    Add a new store to the directory. Requires authentication.

    `name` is required; `location` is optional (defaults to null).
    Returns the created store with its assigned id.
    """
    db_store = Store(name=store.name, location=store.location)
    db.add(db_store)
    db.commit()
    db.refresh(db_store)
    return db_store
