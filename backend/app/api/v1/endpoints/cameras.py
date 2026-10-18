"""
backend/app/api/v1/endpoints/cameras.py

Camera directory — read and create. Each camera belongs to a store and watches
one aisle.

GET  /cameras        List all cameras.
POST /cameras        Create a new camera (store_id must reference an existing store).
"""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.auth_deps import get_current_user
from app.core.database import get_db
from app.models.camera import Camera
from app.models.store import Store
from app.schemas.camera import CameraCreate, CameraResponse

router = APIRouter()


@router.get(
    "",
    response_model=List[CameraResponse],
    summary="List cameras for the current user's store",
)
def list_cameras(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> List[CameraResponse]:
    """Return the cameras belonging to the current user's store, ordered by id.

    Tenant-scoped: a caller never sees cameras belonging to another store.
    """
    return (
        db.query(Camera)
        .filter(Camera.store_id == current_user.store_id)
        .order_by(Camera.id)
        .all()
    )


@router.post(
    "",
    response_model=CameraResponse,
    summary="Create a new camera",
    status_code=status.HTTP_201_CREATED,
)
def create_camera(
    camera: CameraCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CameraResponse:
    """
    Add a new camera to the current user's store.

    ``store_id`` comes from the authenticated user, never from the client, so a
    caller cannot create a camera in another store. ``aisle`` and ``name`` are
    required strings. Returns the created camera with its assigned id.
    """
    store = db.get(Store, current_user.store_id)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Your store was not found.",
        )
    db_camera = Camera(
        store_id=current_user.store_id,
        aisle=camera.aisle,
        name=camera.name,
    )
    db.add(db_camera)
    db.commit()
    db.refresh(db_camera)
    return db_camera
