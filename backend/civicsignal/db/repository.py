"""
SQLite persistence behind a small repository interface.

Synchronous sqlite3 with a connection per operation (thread-safe for use via
asyncio.to_thread). Callers depend only on the public methods, so a
PostgreSQL implementation can replace this class later.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from ..models import Investigation, InvestigationSummary
from ..serp.fixtures import normalize_query

SCHEMA_VERSION = 2
_SCHEMA_FILE = Path(__file__).with_name("schema.sql")


class Repository:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def init_schema(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"Database schema v{version} is newer than this code (v{SCHEMA_VERSION})."
                )
            if 0 < version < SCHEMA_VERSION:
                # No migrations yet (pre-release): development databases are disposable.
                raise RuntimeError(
                    f"Database {self.path} uses schema v{version}; this code needs v{SCHEMA_VERSION}. "
                    "Delete the development database file and restart."
                )
            conn.executescript(_SCHEMA_FILE.read_text(encoding="utf-8"))
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()

    def ping(self) -> bool:
        try:
            with closing(self._connect()) as conn:
                conn.execute("SELECT 1 FROM investigations LIMIT 1")
            return True
        except sqlite3.Error:
            return False

    def save_investigation(
        self, inv: Investigation, raw_responses: dict[str, dict[str, Any]] | None = None
    ) -> None:
        """Insert or replace an investigation and all its child rows in one transaction."""
        raw_responses = raw_responses or {}
        dump = lambda m: m.model_dump_json()  # noqa: E731
        with closing(self._connect()) as conn, conn:
            conn.execute("DELETE FROM investigations WHERE id = ?", (inv.id,))
            conn.execute(
                "INSERT INTO investigations (id, query, normalized_query, status, integrity_status,"
                " as_of_date, created_at, completed_at, report) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    inv.id,
                    inv.query,
                    normalize_query(inv.query),
                    inv.status.value,
                    inv.integrity_status.value,
                    inv.as_of_date.isoformat(),
                    inv.created_at.isoformat(),
                    inv.completed_at.isoformat() if inv.completed_at else None,
                    inv.model_dump_json(),
                ),
            )
            for run in inv.searches:
                query_id = f"qry_{run.id}"
                conn.execute(
                    "INSERT INTO queries (id, investigation_id, text, reason, reason_detail) VALUES (?,?,?,?,?)",
                    (query_id, inv.id, run.request.q, run.request.reason.value, run.request.reason_detail),
                )
                raw = raw_responses.get(run.id)
                conn.execute(
                    "INSERT INTO search_runs (id, investigation_id, query_id, engine, status, data_mode,"
                    " fetched_at, latency_ms, result_count, serpapi_search_id, error, data, raw_response)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run.id,
                        inv.id,
                        query_id,
                        run.request.engine,
                        run.status.value,
                        run.data_mode.value if run.data_mode else None,
                        run.fetched_at.isoformat() if run.fetched_at else None,
                        run.latency_ms,
                        run.result_count,
                        run.serpapi_search_id,
                        run.error,
                        dump(run),
                        json.dumps(raw, ensure_ascii=False) if raw is not None else None,
                    ),
                )
            conn.executemany(
                "INSERT INTO sources (id, investigation_id, canonical_url, registrable_domain, data)"
                " VALUES (?,?,?,?,?)",
                [(s.id, inv.id, s.canonical_url, s.registrable_domain, dump(s)) for s in inv.sources],
            )
            conn.executemany(
                "INSERT INTO search_results (id, investigation_id, search_run_id, source_id, result_type,"
                " position, canonical_url, data) VALUES (?,?,?,?,?,?,?,?)",
                [
                    (r.id, inv.id, r.search_run_id, r.source_id, r.result_type.value, r.position,
                     r.canonical_url, dump(r))
                    for r in inv.results
                ],
            )
            conn.executemany(
                "INSERT INTO evidence_spans (id, investigation_id, result_id, data) VALUES (?,?,?,?)",
                [(e.id, inv.id, e.result_id, dump(e)) for e in inv.evidence_spans],
            )
            conn.executemany(
                "INSERT INTO claim_clusters (id, investigation_id, subject, attribute, has_conflict, data)"
                " VALUES (?,?,?,?,?,?)",
                [(c.id, inv.id, c.subject, c.attribute, int(c.has_conflict), dump(c)) for c in inv.clusters],
            )
            conn.executemany(
                "INSERT INTO claims (id, investigation_id, source_id, result_id, claim_type, subject,"
                " attribute, value, role, data) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (c.id, inv.id, c.source_id, c.result_id, c.claim_type.value, c.subject, c.attribute,
                     c.value, c.role.value, dump(c))
                    for c in inv.claims
                ],
            )
            conn.executemany(
                "INSERT INTO findings (id, investigation_id, detector, kind, scope, strength, data)"
                " VALUES (?,?,?,?,?,?,?)",
                [
                    (f.id, inv.id, f.detector, f.kind, f.scope.value, f.strength.value, dump(f))
                    for f in inv.findings
                ],
            )
            conn.executemany(
                "INSERT INTO detector_runs (investigation_id, detector, status, message, latency_ms,"
                " finding_count) VALUES (?,?,?,?,?,?)",
                [
                    (inv.id, d.detector, d.status.value, d.message, d.latency_ms, d.finding_count)
                    for d in inv.detector_runs
                ],
            )

    def get_investigation(self, investigation_id: str) -> Investigation | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT report FROM investigations WHERE id = ?", (investigation_id,)
            ).fetchone()
        return Investigation.model_validate_json(row["report"]) if row else None

    def list_investigations(self, limit: int = 20, offset: int = 0) -> list[InvestigationSummary]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT report FROM investigations ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        summaries = []
        for row in rows:
            inv = Investigation.model_validate_json(row["report"])
            summaries.append(
                InvestigationSummary(
                    id=inv.id,
                    query=inv.query,
                    created_at=inv.created_at,
                    status=inv.status,
                    integrity_status=inv.integrity_status,
                    total_search_results=inv.metrics.total_search_results,
                    unique_sources=inv.metrics.unique_sources,
                    finding_events=inv.metrics.finding_events,
                )
            )
        return summaries

    def get_raw_response(self, investigation_id: str, search_run_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT raw_response FROM search_runs WHERE investigation_id = ? AND id = ?",
                (investigation_id, search_run_id),
            ).fetchone()
        if not row or row["raw_response"] is None:
            return None
        return json.loads(row["raw_response"])
