"""
Capture the CivicSignal benchmark/demo query set as real SerpApi fixtures.

Run from the backend/ directory in a terminal where SERPAPI_API_KEY is set:

    ..\\.venv\\Scripts\\python -m civicsignal.serp.capture_batch

Spends one SerpApi search per query. Queries that already have a captured
fixture are skipped (use --force to recapture). The key is read from the
environment only and is never printed; after capturing, every fixture file
is scanned to confirm the key does not appear in it.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter

from ..models import ResultType
from .capture import capture_one, capture_settings, fixture_name
from .errors import SerpApiAuthError, SerpApiError, SerpApiQuotaError
from .parsing import parse_response

# (query, why it is in the set)
QUERIES: list[tuple[str, str]] = [
    ("Aadhaar free update last date 2026", "deadline with repeated extensions; stale/conflict candidate"),
    ("PM Kisan next installment date 2026", "recurring payout; speculative 'expected' dates"),
    ("NEET UG 2026 exam date", "exam schedule; possibly clean"),
    ("Telangana Rythu Bharosa installment 2026", "state scheme; regional source landscape"),
    ("West Bengal Ayushman Bharat deadline 2026", "eligibility/deadline; duplication candidate"),
    ("PAN Aadhaar link last date 2026", "regulatory deadline with past extensions"),
    ("RTI application fee central government", "CLEAN CONTROL: long-stable statutory fee"),
    ("Indian passport validity for adults", "CLEAN CONTROL: long-stable rule"),
]


def _summary(raw: dict) -> str:
    items = parse_response(raw, "google")
    counts = Counter(i.result_type.value for i in items)
    organic = counts.get(ResultType.ORGANIC.value, 0)
    extras = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()) if k != ResultType.ORGANIC.value)
    return f"{organic} organic" + (f" ({extras})" if extras else "")


async def _run(force: bool) -> int:
    settings = capture_settings()
    if not settings.serpapi_configured:
        print("ERROR: SERPAPI_API_KEY is not set in this terminal.", file=sys.stderr)
        return 1
    secret = settings.serpapi_api_key.get_secret_value()  # type: ignore[union-attr]
    fixtures_dir = settings.fixtures_dir
    failures = 0

    for i, (query, why) in enumerate(QUERIES, start=1):
        path = fixtures_dir / f"{fixture_name('google', query)}.json"
        if path.exists() and not force:
            print(f"[{i}/{len(QUERIES)}] SKIP (already captured): {query}")
            continue
        try:
            path, response = await capture_one(settings, query, note=why)
        except (SerpApiAuthError, SerpApiQuotaError) as exc:
            print(f"[{i}/{len(QUERIES)}] FAILED: {query} -> {exc}")
            print("Stopping: further searches would fail the same way.")
            return 1
        except SerpApiError as exc:
            failures += 1
            print(f"[{i}/{len(QUERIES)}] FAILED: {query} -> {type(exc).__name__}: {exc}")
            continue
        print(f"[{i}/{len(QUERIES)}] OK: {query} -> {_summary(response.raw)} -> {path.name}")

    leaked = [p.name for p in fixtures_dir.glob("*.json") if secret in p.read_text(encoding="utf-8")]
    print()
    print(f"Fixtures directory: {fixtures_dir}")
    print(f"API key found in any saved fixture: {'YES -> ' + json.dumps(leaked) if leaked else 'NO'}")
    print(f"Failures: {failures}")
    return 1 if failures or leaked else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture the CivicSignal query set as fixtures")
    parser.add_argument("--force", action="store_true", help="recapture queries that already have fixtures")
    sys.exit(asyncio.run(_run(parser.parse_args().force)))


if __name__ == "__main__":
    main()
