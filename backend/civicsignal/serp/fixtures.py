"""
Fixture store for mock/demo mode.

Fixture file format (backend/fixtures/serpapi/*.json):

    {
      "civicsignal_fixture": {
        "engine": "google",
        "q": "Aadhaar free update last date 2026",
        "params": {"gl": "in", "hl": "en"},
        "kind": "captured" | "reconstructed",
        "captured_at": "2026-09-29T...Z",
        "note": "..."
      },
      "response": { ...raw SerpApi JSON... }
    }

"captured" = a real SerpApi response saved by `python -m civicsignal.serp.capture`.
"reconstructed" = rebuilt by hand from earlier processed output; NOT a raw response.
The kind is surfaced in every investigation that uses it.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..logging_setup import log_event

logger = logging.getLogger("civicsignal.serp.fixtures")


def normalize_query(q: str) -> str:
    return " ".join(q.lower().split())


# Params that change WHAT is searched: a fixture without them must not answer a request with them
# (e.g. a "past month only" request must never be served the unfiltered base response).
_STRICT_PARAMS = {"tbs"}


def _params_match(fixture_params: dict[str, Any], request_params: dict[str, Any]) -> bool:
    for key in set(fixture_params) | set(request_params):
        in_f, in_r = key in fixture_params, key in request_params
        if in_f and in_r and str(fixture_params[key]) != str(request_params[key]):
            return False
        if in_f != in_r and key in _STRICT_PARAMS:
            return False
    return True


@dataclass
class Fixture:
    engine: str
    q: str
    params: dict[str, Any]
    kind: str
    captured_at: datetime
    note: str
    response: dict[str, Any]
    path: Path


class FixtureStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _load_all(self) -> list[Fixture]:
        fixtures: list[Fixture] = []
        if not self.directory.exists():
            return fixtures
        for path in sorted(self.directory.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                meta = data["civicsignal_fixture"]
                fixtures.append(
                    Fixture(
                        engine=meta.get("engine", "google"),
                        q=meta["q"],
                        params=meta.get("params", {}),
                        kind=meta.get("kind", "reconstructed"),
                        captured_at=datetime.fromisoformat(
                            meta.get("captured_at", "1970-01-01T00:00:00+00:00")
                        ),
                        note=meta.get("note", ""),
                        response=data["response"],
                        path=path,
                    )
                )
            except (OSError, ValueError, KeyError) as exc:
                log_event(logger, "fixture_unreadable", logging.WARNING, path=path.name, error=str(exc))
        return fixtures

    def find(self, engine: str, q: str, params: dict[str, Any] | None = None) -> Fixture | None:
        """
        Match on engine + normalized query. Real captures are preferred over
        reconstructed fixtures; then a fixture whose params also match.
        """
        wanted = normalize_query(q)
        candidates = [f for f in self._load_all() if f.engine == engine and normalize_query(f.q) == wanted]
        if not candidates:
            return None
        candidates.sort(key=lambda f: f.kind != "captured")
        params = params or {}
        for f in candidates:
            if _params_match(f.params, params):
                return f
        return None

    def all(self) -> list[Fixture]:
        return self._load_all()

    def available_queries(self) -> list[str]:
        return [f.q for f in self._load_all()]

    def save(
        self,
        name: str,
        engine: str,
        q: str,
        params: dict[str, Any],
        response: dict[str, Any],
        kind: str = "captured",
        note: str = "",
        captured_at: datetime | None = None,
    ) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        payload = {
            "civicsignal_fixture": {
                "engine": engine,
                "q": q,
                "params": params,
                "kind": kind,
                "captured_at": (captured_at or datetime.now(timezone.utc)).isoformat(),
                "note": note,
            },
            "response": response,
        }
        path = self.directory / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
