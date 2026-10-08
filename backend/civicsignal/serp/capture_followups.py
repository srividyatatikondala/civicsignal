"""
List (and optionally capture) the follow-up searches the planner requests
for each captured base fixture.

    ..\\.venv\\Scripts\\python -m civicsignal.serp.capture_followups            # dry run: spends nothing
    ..\\.venv\\Scripts\\python -m civicsignal.serp.capture_followups --execute  # live: one search per follow-up

The dry run replays every base fixture offline, runs the planner, and prints
each planned follow-up with its reason and triggering evidence. --execute
performs exactly those searches through SerpApi (SERPAPI_API_KEY from the
environment) and saves them as captured fixtures, skipping any already saved.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from datetime import date
from pathlib import Path

from ..config import load_settings
from ..db import Repository
from ..models import InvestigateRequest, SearchReason, SearchRequest
from ..orchestrator import Investigator
from .capture import capture_settings, fixture_name
from .client import SerpApiClient
from .errors import SerpApiAuthError, SerpApiError, SerpApiQuotaError
from .fixtures import FixtureStore


def _name(request: SearchRequest) -> str:
    tag = request.reason.value
    if request.params.get("tbs"):
        tag += "_" + str(request.params["tbs"]).replace(":", "")
    return f"{fixture_name(request.engine, request.q)}__{tag}"


async def _planned(as_of: date) -> list[tuple[str, SearchRequest, list[str]]]:
    settings = load_settings().model_copy(update={"mock_serpapi": True, "cache_enabled": False})
    with tempfile.TemporaryDirectory() as tmp:
        settings = settings.model_copy(update={"database_path": Path(tmp) / "plan.sqlite3"})
        repo = Repository(settings.database_path)
        repo.init_schema()
        investigator = Investigator(settings, SerpApiClient(settings), repo, today=lambda: as_of)
        out = []
        store = FixtureStore(settings.fixtures_dir)
        bases = [f for f in store.all() if f.kind == "captured" and not f.note.startswith("Follow-up (")]
        for f in bases:
            inv = await investigator.investigate(InvestigateRequest(query=f.q))
            for run in inv.searches:
                if run.request.reason != SearchReason.BASE_QUERY and store.find(
                    run.request.engine, run.request.q, dict(run.request.params)
                ) is None:
                    out.append((f.q, run.request, run.triggered_by))
        return out


async def _main(execute: bool, as_of: date) -> int:
    planned = await _planned(as_of)
    print(f"{len(planned)} follow-up search(es) planned without a captured fixture:\n")
    for q, req, why in planned:
        params = {k: v for k, v in req.params.items() if k in ("tbs", "hl")}
        print(f"- [{req.reason.value}] engine={req.engine} q={req.q!r} {json.dumps(params) if params else ''}")
        print(f"    for: {q}")
        for w in why:
            print(f"    because: {w}")
    if not execute:
        print("\nDry run: no SerpApi searches were made. Re-run with --execute to capture them.")
        return 0

    settings = capture_settings()
    if not settings.serpapi_configured:
        print("ERROR: SERPAPI_API_KEY is not set in this terminal.", file=sys.stderr)
        return 1
    client = SerpApiClient(settings, cache=None)
    store = FixtureStore(settings.fixtures_dir)
    secret = settings.serpapi_api_key.get_secret_value()  # type: ignore[union-attr]
    for q, req, why in planned:
        try:
            response = await client.search(req)
        except (SerpApiAuthError, SerpApiQuotaError) as exc:
            print(f"FAILED (stopping): {exc}")
            return 1
        except SerpApiError as exc:
            print(f"FAILED: {req.q} -> {exc}")
            continue
        path = store.save(_name(req), req.engine, req.q, dict(req.params), response.raw, kind="captured",
                          note=f"Follow-up ({req.reason.value}) for: {q}. Because: " + " | ".join(why),
                          captured_at=response.fetched_at)
        print(f"OK: {path.name}")
    leaked = [p.name for p in store.directory.glob("*.json") if secret in p.read_text(encoding="utf-8")]
    print(f"API key found in any saved fixture: {'YES ' + str(leaked) if leaked else 'NO'}")
    return 1 if leaked else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="List / capture planner follow-up searches")
    parser.add_argument("--execute", action="store_true", help="perform the live SerpApi searches")
    parser.add_argument("--as-of", default="2026-10-01", help="analysis date used when replaying fixtures")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # evidence text contains non-ASCII punctuation
    sys.exit(asyncio.run(_main(args.execute, date.fromisoformat(args.as_of))))


if __name__ == "__main__":
    main()
