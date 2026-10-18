"""
backend/app/schemas/common.py

Field types shared by the response schemas.

The timestamps are stored in UTC but SQLite (the default DATABASE_URL in local
development) silently drops the timezone: a `DateTime(timezone=True)` column comes
back as a *naive* datetime. Pydantic then serializes it as `2026-10-17T09:29:52`
with no offset, and the browser's `new Date(...)` interprets that as local time —
so a capture taken at 14:59 IST was displayed as 09:29. Serializing every
timestamp as explicit UTC (`...Z`) lets the client convert to the viewer's zone.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Optional

from pydantic import PlainSerializer


def as_utc_iso(value: datetime) -> str:
    """Render a datetime as ISO-8601 UTC with an explicit offset.

    Every timestamp written by this app is UTC, so a naive value (what SQLite
    hands back) is interpreted as UTC rather than as local time.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _optional_as_utc_iso(value: Optional[datetime]) -> Optional[str]:
    return None if value is None else as_utc_iso(value)


#: A required timestamp that always serializes with a UTC offset.
UTCDateTime = Annotated[
    datetime,
    PlainSerializer(as_utc_iso, return_type=str, when_used="json"),
]

#: A nullable timestamp that always serializes with a UTC offset.
OptionalUTCDateTime = Annotated[
    Optional[datetime],
    PlainSerializer(_optional_as_utc_iso, return_type=Optional[str], when_used="json"),
]
