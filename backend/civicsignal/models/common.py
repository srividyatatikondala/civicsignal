from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    """Short, prefixed, collision-safe id, e.g. 'inv_3f2a9c...'."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class DataMode(str, Enum):
    """Where search data came from. Always shown to users — demo data is never presented as live."""

    LIVE = "live"  # fetched from SerpApi during this investigation
    CACHED = "cached"  # served from the local response cache (originally live)
    MOCK = "mock"  # served from a fixture file (captured or reconstructed)
