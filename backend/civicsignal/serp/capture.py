"""
Capture real SerpApi responses as fixtures for tests and demo mode.

    python -m civicsignal.serp.capture --query "Aadhaar free update last date 2026"
    python -m civicsignal.serp.capture --query "..." --engine google_news --name aadhaar_news

Requires SERPAPI_API_KEY in the environment. The saved file has secrets
scrubbed and is marked kind="captured" with the capture timestamp, so demo
mode can label it honestly. Run from the backend/ directory (or with
backend/ on PYTHONPATH).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from ..config import Settings, load_settings
from ..models import SearchRequest, SerpApiResponse
from .client import SerpApiClient
from .errors import SerpApiError
from .fixtures import FixtureStore


def fixture_name(engine: str, query: str) -> str:
    # kept short so a clone into a deep Windows folder stays under the 260-character path limit
    slug = re.sub(r"[^a-z0-9]+", "_", query.lower()).strip("_")
    if len(slug) > 45:  # cut at a whole word
        slug = slug[:46].rsplit("_", 1)[0]
    return f"{engine}__{slug}"


def capture_settings() -> Settings:
    """Live, uncached settings: a capture must reflect what SerpApi returns now."""
    return load_settings().model_copy(update={"mock_serpapi": False, "cache_enabled": False})


async def capture_one(
    settings: Settings,
    query: str,
    *,
    engine: str = "google",
    gl: str | None = None,
    hl: str | None = None,
    num: int | None = None,
    name: str | None = None,
    note: str = "",
) -> tuple[Path, SerpApiResponse]:
    """Run one live search and save it as a captured fixture. Raises SerpApiError."""
    client = SerpApiClient(settings, cache=None)
    params: dict[str, str | int] = {"gl": gl or settings.default_gl, "hl": hl or settings.default_hl}
    if engine == "google":
        params["num"] = num or settings.max_results_per_search
    response = await client.search(SearchRequest(engine=engine, q=query, params=params))
    path = FixtureStore(settings.fixtures_dir).save(
        name=name or fixture_name(engine, query),
        engine=engine,
        q=query,
        params=params,
        response=response.raw,
        kind="captured",
        note=note,
        captured_at=response.fetched_at,
    )
    return path, response


async def _main(args: argparse.Namespace) -> int:
    try:
        path, _ = await capture_one(
            capture_settings(),
            args.query,
            engine=args.engine,
            gl=args.gl,
            hl=args.hl,
            num=args.num,
            name=args.name,
            note=args.note or "",
        )
    except SerpApiError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {path}", file=sys.stderr)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture a SerpApi response as a fixture")
    parser.add_argument("--query", required=True)
    parser.add_argument("--engine", default="google")
    parser.add_argument("--gl")
    parser.add_argument("--hl")
    parser.add_argument("--num", type=int)
    parser.add_argument("--name", help="fixture file name (without .json)")
    parser.add_argument("--note", help="free-text note stored with the fixture")
    sys.exit(asyncio.run(_main(parser.parse_args())))


if __name__ == "__main__":
    main()
