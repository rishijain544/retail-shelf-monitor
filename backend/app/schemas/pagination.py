"""
backend/app/schemas/pagination.py

Generic envelope for paginated list endpoints. `Page[SomeResponse]` is used as a
FastAPI response_model, which renders cleanly in /docs (schema "Page_SomeResponse_").
"""
from __future__ import annotations

from typing import Generic, List, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """A single page of results plus the metadata a client needs to page further."""
    items: List[T]
    total: int   # total rows matching the query, ignoring limit/offset
    limit: int   # max rows requested for this page
    offset: int  # rows skipped before this page
