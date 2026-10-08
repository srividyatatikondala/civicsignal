"""
CivicSignal — Prototype 1: Staleness Detector (CLI, compatibility layer)

Pipeline:
  Query -> SerpApi organic results -> extract date-bearing claims
        -> classify each claim's role (deadline / past-event / open-ended /
           historical-reference / unknown) -> compare against today
        -> emit a JSON report with an evidence trail for every flag.

Since Phase 2 the date extraction and role classification come from the
shared engine in backend/civicsignal/detectors/staleness/ (nearest cue in
the same clause, till/until, ISO dates, year-wrapping ranges). This file
keeps the original CLI and analyze_result() interface working. The original
prototype is preserved in _backup/prototype-2026-09-29/.

Usage:
    export SERPAPI_API_KEY="your_key_here"
    python staleness_detector.py --query "Aadhaar free update last date 2026"
    python staleness_detector.py --queries queries.txt --out report.json

Requires: requests (CLI only)
"""

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

_BACKEND = str(Path(__file__).resolve().parent / "backend")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

from civicsignal.detectors.staleness import StalenessConfig, analyze_text, severity  # noqa: E402
from civicsignal.models import EvidenceField, TemporalRole  # noqa: E402

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"

# Engine roles -> the prototype's original role vocabulary. "expected" dates
# were reported as "deadline" by the prototype, so that is preserved here.
_LEGACY_ROLE = {
    TemporalRole.DEADLINE: "deadline",
    TemporalRole.EXPECTED_EVENT: "deadline",
    TemporalRole.PAST_EVENT: "past_event",
    TemporalRole.OPEN_ENDED: "open_ended",
    TemporalRole.HISTORICAL_REFERENCE: "historical_reference",
    TemporalRole.PUBLICATION: "publication",
    TemporalRole.UPDATED: "publication",
    TemporalRole.ELIGIBILITY_CUTOFF: "unknown",
    TemporalRole.UNKNOWN: "unknown",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ExcludedDate:
    source_domain: str
    source_url: str
    date_start: str
    role: str
    context: str


@dataclass
class StaleClaim:
    source_domain: str
    source_url: str
    position: int
    date_precision: str  # "day" | "month" | "month_range"
    date_start: str  # ISO date
    date_end: str  # ISO date (== date_start when precision == "day")
    role: str  # deadline / past_event / open_ended / historical_reference / unknown
    context: str  # the clause the date was found in
    snippet: str  # full original SerpApi snippet, for the evidence trail
    days_expired: Optional[int]
    severity: str  # high / medium / low


@dataclass
class StalenessReport:
    query: str
    current_date: str
    total_results_checked: int
    total_dates_found: int
    excluded_open_ended: int
    excluded_historical: int
    excluded_unknown_role: int
    excluded_publication: int = 0
    stale_claims: list = field(default_factory=list)
    excluded_samples: list = field(default_factory=list)  # visibility into unknown-role drops


# ---------------------------------------------------------------------------
# SerpApi fetch
# ---------------------------------------------------------------------------


def fetch_organic_results(query: str, api_key: str, num: int = 10) -> list[dict]:
    """Call SerpApi's google engine and return the organic_results list."""
    import requests

    params = {"engine": "google", "q": query, "num": num, "gl": "in", "hl": "en", "api_key": api_key}
    resp = requests.get(SERPAPI_ENDPOINT, params=params, timeout=20)
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        raise RuntimeError(f"SerpApi error: {data['error']}")
    return data.get("organic_results", [])


def domain_from_url(url: str) -> str:
    m = re.search(r"https?://(?:www\.)?([^/]+)", url or "")
    return m.group(1) if m else "unknown"


# ---------------------------------------------------------------------------
# Core detector
# ---------------------------------------------------------------------------


def analyze_result(
    result: dict, today: date, config: StalenessConfig = StalenessConfig()
) -> tuple[list[StaleClaim], dict, list[ExcludedDate]]:
    """
    Date extraction + role classification + staleness scoring on one SerpApi
    organic result. Returns (stale_claims, exclusion_counts, excluded_unknown_samples).

    Title and snippet are analyzed as separate fields; a date repeated in both
    (common for YouTube results) is counted once.
    """
    snippet = (result.get("snippet") or "").strip()
    title = (result.get("title") or "").strip()
    position = result.get("position", -1)
    url = result.get("link", "")
    domain = domain_from_url(url)

    exclusions = {"open_ended": 0, "historical": 0, "unknown_role": 0, "publication": 0}
    claims: list[StaleClaim] = []
    excluded_samples: list[ExcludedDate] = []
    seen: set[tuple] = set()

    mentions = analyze_text(snippet, EvidenceField.SNIPPET) + analyze_text(title, EvidenceField.TITLE)
    for m in mentions:
        role = _LEGACY_ROLE[m.role]
        key = (m.date_start, m.date_end, role)
        if key in seen:
            continue  # same underlying claim already counted for this result
        seen.add(key)

        if role == "open_ended":
            exclusions["open_ended"] += 1
            continue
        if role == "historical_reference":
            exclusions["historical"] += 1
            continue
        if role == "publication":
            exclusions["publication"] += 1
            continue
        if role == "unknown":
            exclusions["unknown_role"] += 1
            excluded_samples.append(ExcludedDate(domain, url, m.date_start.isoformat(), role, m.clause))
            continue
        if role != "deadline" or m.date_end >= today:
            continue

        days_expired = (today - m.date_end).days
        rank = position if isinstance(position, int) and position > 0 else None
        claims.append(
            StaleClaim(
                source_domain=domain,
                source_url=url,
                position=position,
                date_precision=m.precision,
                date_start=m.date_start.isoformat(),
                date_end=m.date_end.isoformat(),
                role=role,
                context=m.clause,
                snippet=snippet,
                days_expired=days_expired,
                severity=severity(days_expired, rank, config).value,
            )
        )

    return claims, exclusions, excluded_samples


def run_staleness_detector(query: str, api_key: str, today: Optional[date] = None) -> StalenessReport:
    today = today or date.today()
    results = fetch_organic_results(query, api_key)

    report = StalenessReport(
        query=query,
        current_date=today.isoformat(),
        total_results_checked=len(results),
        total_dates_found=0,
        excluded_open_ended=0,
        excluded_historical=0,
        excluded_unknown_role=0,
    )

    for result in results:
        claims, exclusions, excluded_samples = analyze_result(result, today)
        report.stale_claims.extend(asdict(c) for c in claims)
        report.excluded_samples.extend(asdict(e) for e in excluded_samples)
        report.total_dates_found += len(claims) + sum(exclusions.values())
        report.excluded_open_ended += exclusions["open_ended"]
        report.excluded_historical += exclusions["historical"]
        report.excluded_unknown_role += exclusions["unknown_role"]
        report.excluded_publication += exclusions["publication"]

    order = {"high": 0, "medium": 1, "low": 2}
    report.stale_claims.sort(key=lambda c: order.get(c["severity"], 9))
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="CivicSignal staleness detector prototype")
    parser.add_argument("--query", help="single query to check")
    parser.add_argument("--queries", help="path to a text file, one query per line")
    parser.add_argument("--out", help="write JSON report(s) to this path", default=None)
    parser.add_argument("--today", help="override current date, YYYY-MM-DD (for testing)", default=None)
    args = parser.parse_args()

    api_key = os.environ.get("SERPAPI_API_KEY")
    if not api_key:
        print("ERROR: set SERPAPI_API_KEY in your environment first.", file=sys.stderr)
        sys.exit(1)

    today = date.fromisoformat(args.today) if args.today else date.today()

    queries = []
    if args.query:
        queries.append(args.query)
    if args.queries:
        with open(args.queries, encoding="utf-8") as f:
            queries.extend(line.strip() for line in f if line.strip())
    if not queries:
        print("ERROR: provide --query or --queries", file=sys.stderr)
        sys.exit(1)

    all_reports = []
    for q in queries:
        print(f"Checking: {q}", file=sys.stderr)
        report = run_staleness_detector(q, api_key, today=today)
        all_reports.append(asdict(report))
        print(f"  -> {len(report.stale_claims)} potentially stale claim(s) found", file=sys.stderr)

    output = all_reports if len(all_reports) > 1 else all_reports[0]
    text = json.dumps(output, indent=2, ensure_ascii=False)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"Report written to {args.out}", file=sys.stderr)
    else:
        print(text)


if __name__ == "__main__":
    main()
