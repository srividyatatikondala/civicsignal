"""
FastAPI application.

Run locally (from the project root):
    .venv/Scripts/python -m uvicorn civicsignal.api.app:create_app --factory --app-dir backend --reload

All SerpApi calls happen server-side; no secret is ever included in a response.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .. import __version__
from ..config import Settings, load_settings
from ..db import Repository
from ..llm import get_llm_provider
from ..logging_setup import configure_logging, log_event
from ..models import InvestigateRequest, Investigation, InvestigationSummary
from ..models.report import InvestigationReport
from ..orchestrator import Investigator
from ..report import build_report
from ..serp import SerpApiClient

logger = logging.getLogger("civicsignal.api")


def create_app(
    settings: Settings | None = None,
    client: SerpApiClient | None = None,
    repository: Repository | None = None,
    investigator: Investigator | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level, settings.secret_values())

    repository = repository or Repository(settings.database_path)
    repository.init_schema()
    client = client or SerpApiClient(settings)
    investigator = investigator or Investigator(settings, client, repository)
    llm = get_llm_provider(settings)

    app = FastAPI(
        title="CivicSignal API",
        version=__version__,
        description="Search Information Integrity Analyzer — investigates the search landscape, not a truth oracle.",
    )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):  # pragma: no cover - safety net
        logger.exception("unhandled_error", extra={"fields": {"path": request.url.path}})
        return JSONResponse(status_code=500, content={"detail": "Internal error. See server logs."})

    @app.get("/", include_in_schema=False)
    async def root() -> dict[str, Any]:
        """The backend serves only the API; the dashboard runs separately (frontend/, port 5173)."""
        return {
            "service": "CivicSignal API",
            "mode": "demo (captured SerpApi data)" if settings.mock_serpapi else "live (SerpApi)",
            "dashboard": "http://localhost:5173",
            "api_docs": "/docs",
            "health": "/api/health",
        }

    @app.get("/api/demo-queries")
    async def demo_queries() -> dict[str, Any]:
        """Questions answerable from captured SerpApi data (mock/demo mode only)."""
        if not settings.mock_serpapi:
            return {"mock_mode": False, "queries": []}
        fixtures = await asyncio.to_thread(client.fixtures.all)
        queries = [
            {"query": f.q, "captured_at": f.captured_at.date().isoformat(), "note": f.note}
            for f in fixtures
            if f.kind == "captured" and not f.note.startswith("Follow-up (")
        ]
        return {"mock_mode": True, "queries": queries}

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        db_ok = await asyncio.to_thread(repository.ping)
        return {
            "status": "ok" if db_ok else "degraded",
            "version": __version__,
            "serpapi": {
                "configured": settings.serpapi_configured,
                "mock_mode": settings.mock_serpapi,
                "cache_enabled": settings.cache_enabled,
            },
            "llm": {"provider": llm.name, "available": llm.available},
            "database": {"ok": db_ok},
            "limits": {
                "max_searches_per_investigation": settings.max_searches_per_investigation,
                "max_results_per_search": settings.max_results_per_search,
            },
        }

    @app.post("/api/investigate", response_model=Investigation)
    async def investigate(body: InvestigateRequest) -> Investigation:
        log_event(logger, "investigate_request", query=body.query)
        return await investigator.investigate(body)

    @app.get("/api/investigations", response_model=list[InvestigationSummary])
    async def list_investigations(
        limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)
    ) -> list[InvestigationSummary]:
        return await asyncio.to_thread(repository.list_investigations, limit, offset)

    @app.get("/api/investigations/{investigation_id}", response_model=Investigation)
    async def get_investigation(investigation_id: str) -> Investigation:
        inv = await asyncio.to_thread(repository.get_investigation, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="Investigation not found.")
        return inv

    @app.get("/api/investigations/{investigation_id}/report", response_model=InvestigationReport)
    async def get_report(investigation_id: str) -> InvestigationReport:
        """Evidence-linked investigation report (Evidence Map), derived from the stored investigation."""
        inv = await asyncio.to_thread(repository.get_investigation, investigation_id)
        if inv is None:
            raise HTTPException(status_code=404, detail="Investigation not found.")
        return build_report(inv)

    @app.get("/api/investigations/{investigation_id}/searches/{search_id}/raw")
    async def get_raw_response(investigation_id: str, search_id: str) -> dict[str, Any]:
        """The full, unmodified (secret-scrubbed) SerpApi response behind a search — for traceability."""
        raw = await asyncio.to_thread(repository.get_raw_response, investigation_id, search_id)
        if raw is None:
            raise HTTPException(status_code=404, detail="Raw response not found.")
        return raw

    return app
