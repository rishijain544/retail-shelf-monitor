"""
backend/app/core/clock.py

One place for the aware-vs-naive datetime trap.

Columns are declared ``DateTime(timezone=True)`` and always written with
``datetime.now(timezone.utc)``. SQLite, however, has no timestamp type: it stores the
string and hands back a *naive* datetime. Comparing a naive value loaded from SQLite
with an aware ``now`` raises ``TypeError: can't compare offset-naive and
offset-aware datetimes`` — which turns a lockout check into a 500 only on the
developer's local database, never in Postgres.

``as_utc`` normalises a loaded value before any comparison, so the same code path works
on both backends.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    """The current time as an aware UTC datetime."""
    return datetime.now(timezone.utc)


def as_utc(value: Optional[datetime]) -> Optional[datetime]:
    """Return `value` as an aware UTC datetime (SQLite hands back naive ones)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
