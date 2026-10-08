"""
File-based response cache keyed by SearchRequest.cache_key() (which never
includes the API key). Cuts API spend during development and makes repeat
investigations reproducible within the TTL.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..logging_setup import log_event

logger = logging.getLogger("civicsignal.serp.cache")


class ResponseCache:
    def __init__(self, directory: Path, ttl_hours: float):
        self.directory = Path(directory)
        self.ttl = timedelta(hours=ttl_hours)

    def _path(self, key: str) -> Path:
        return self.directory / f"{key}.json"

    def get(self, key: str, now: datetime | None = None) -> tuple[dict[str, Any], datetime] | None:
        """Return (raw_response, originally_fetched_at) if fresh, else None."""
        path = self._path(key)
        if not path.exists():
            return None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            fetched_at = datetime.fromisoformat(entry["fetched_at"])
            raw = entry["raw"]
        except (OSError, ValueError, KeyError) as exc:
            log_event(logger, "cache_entry_unreadable", logging.WARNING, key=key, error=str(exc))
            return None
        now = now or datetime.now(timezone.utc)
        if now - fetched_at > self.ttl:
            return None
        return raw, fetched_at

    def set(self, key: str, raw: dict[str, Any], fetched_at: datetime, request: dict) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            entry = {"fetched_at": fetched_at.isoformat(), "request": request, "raw": raw}
            self._path(key).write_text(json.dumps(entry, ensure_ascii=False), encoding="utf-8")
        except OSError as exc:  # a cache write failure must never fail a search
            log_event(logger, "cache_write_failed", logging.WARNING, key=key, error=str(exc))
