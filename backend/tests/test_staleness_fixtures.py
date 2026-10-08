"""
Staleness regression tests on the 8 REAL SerpApi captures (2026-10-01).

as_of_date is pinned to the capture date so results are reproducible.
Expected findings were set by manual reading of each snippet; they are a
regression snapshot, not a labelled benchmark with precision/recall.

  EXPECTED          findings judged correct ("potentially stale" is a fair description)
  KNOWN_FALSE_POS   findings judged wrong but not fixable with current rules;
                    each has a strict-xfail test so a future fix is noticed
"""

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from civicsignal.config import BACKEND_DIR
from civicsignal.db import Repository
from civicsignal.models import InvestigateRequest
from civicsignal.orchestrator import Investigator
from civicsignal.serp import SerpApiClient

from .conftest import base_fixture_queries

CAPTURE_DATE = date(2026, 10, 1)
FIXTURES = BACKEND_DIR / "fixtures" / "serpapi"

# query -> {(link, date_end, strength)}
EXPECTED: dict[str, set[tuple[str, str, str]]] = {
    "PAN Aadhaar link last date 2026": {
        ("https://www.facebook.com/ISHNews/videos/now-apply-for-instant-e-pan-through-aadhaar/750857548674443/",
         "2019-12-31", "high"),
        ("https://www.moneycontrol.com/news/business/personal-finance/pan-aadhaar-linking-do-it-before-january-1-to-avoid-disruptions-13667290.html",
         "2025-12-31", "high"),
    },
    "PM Kisan next installment date 2026": {
        ("https://governmentjobhub.com/pm-kisan-23rd-installment-date/", "2026-06-20", "high"),
        ("https://m.dailyhunt.in/news/india/english/news24online-epaper-newsonline/pm+kisan+samman+nidhi+yojana+23rd+installment+date+2026+big+update+for+farmers+rs+2000+to+be+credited+by+know+eligibility+status+check+process-newsid-n706054541",
         "2026-07-31", "high"),
        ("https://www.latestly.com/india/news/pm-kisan-23rd-installment-date-who-will-get-the-next-payment-and-who-may-miss-out-7381487.html",
         "2026-07-31", "high"),
    },
    "West Bengal Ayushman Bharat deadline 2026": {
        ("https://www.instagram.com/p/DY9Z3FdmAob/", "2024-09-30", "high"),  # "Deadline: ... by Sept 30, 2024"
        ("https://www.instagram.com/reel/DYs7SdwsM65/?hl=en", "2024-09-30", "high"),  # "... by Sept 30, 2024" (by-rule)
    },
    "Telangana Rythu Bharosa installment 2026": {
        ("https://x.com/PingtvIndia/status/2041430020204490761", "2026-04-15", "medium"),  # "credited by April 15, 2026"
    },
    "Aadhaar free update last date 2026": set(),  # results were off-topic on capture day
    "NEET UG 2026 exam date": set(),
    "RTI application fee central government": set(),  # clean control
    "Indian passport validity for adults": set(),  # clean control
}

# Historical narration with ambiguous tense: "CBDT set 31 December 2025 as the deadline ...,
# and unlinked PANs became inoperative from 1 January 2026". "set" is past AND present tense,
# so the rules cannot tell this account of history from a current claim.
ALLINDIAITR = "https://www.allindiaitr.com/pan-aadhaar-link-inoperative-pan"
KNOWN_FALSE_POS = {
    "PAN Aadhaar link last date 2026": {(ALLINDIAITR, "2025-12-31", "medium")},
}


@pytest.fixture(scope="module")
def investigations(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("fixtures_run")
    from civicsignal.config import Settings

    settings = Settings(mock_serpapi=True, fixtures_dir=FIXTURES, cache_enabled=False, database_path=tmp / "db.sqlite3")
    repo = Repository(settings.database_path)
    repo.init_schema()
    inv = Investigator(settings, SerpApiClient(settings), repo, today=lambda: CAPTURE_DATE)
    out = {}
    for q in base_fixture_queries(FIXTURES):
        # Phase 2-5 snapshot: analysis of the BASE search only (follow-ups have their own snapshot)
        out[q] = asyncio.run(inv.investigate(InvestigateRequest(query=q, follow_ups=False)))
    return out


def _findings(inv) -> set[tuple[str, str, str]]:
    results = {r.id: r for r in inv.results}
    return {
        (results[f.result_ids[0]].link, f.date_end, f.strength.value)
        for f in inv.findings
        if f.kind == "staleness"
    }


def test_all_eight_real_captures_load(investigations):
    assert set(investigations) == set(EXPECTED)
    assert all(i.status.value == "completed" for i in investigations.values())


@pytest.mark.parametrize("query", sorted(EXPECTED))
def test_expected_findings_present(investigations, query):
    assert EXPECTED[query] <= _findings(investigations[query])


@pytest.mark.parametrize("query", sorted(EXPECTED))
def test_no_unexpected_findings(investigations, query):
    allowed = EXPECTED[query] | KNOWN_FALSE_POS.get(query, set())
    assert _findings(investigations[query]) <= allowed


@pytest.mark.parametrize("query", ["RTI application fee central government", "Indian passport validity for adults"])
def test_clean_controls_have_no_findings(investigations, query):
    inv = investigations[query]
    assert inv.findings == []  # neither staleness nor contradiction
    assert inv.integrity_status.value == "NOT_ASSESSED"  # never "CLEAR" while detectors are missing


@pytest.mark.xfail(strict=True, reason="KNOWN LIMITATION: ambiguous-tense historical narration ('CBDT set ... as the deadline')")
def test_known_false_positive_allindiaitr_historical_narration(investigations):
    links = {link for link, _, _ in _findings(investigations["PAN Aadhaar link last date 2026"])}
    assert ALLINDIAITR not in links


def test_findings_point_to_exact_evidence(investigations):
    """Every finding (staleness or contradiction) resolves to exact substrings of retrieved text."""
    for inv in investigations.values():
        results = {r.id: r for r in inv.results}
        spans = {e.id: e for e in inv.evidence_spans}
        for f in inv.findings:
            if f.kind == "authority":
                # evidence is the classification signal recorded on every source, not quoted text
                assert all(s.type_signals for s in inv.sources)
                continue
            assert f.evidence_span_ids
            if f.kind == "staleness":
                assert f.source_ids == [results[f.result_ids[0]].source_id]
            for eid in f.evidence_span_ids:
                e = spans[eid]
                r = results[e.result_id]
                assert e.result_id in f.result_ids
                assert e.source_id is None or e.source_id in f.source_ids  # e.g. "People also ask" has no URL
                field_text = r.title if e.field.value == "title" else r.snippet
                assert field_text[e.start : e.end] == e.text


def test_source_and_event_counts_are_separate(investigations):
    wb = investigations["West Bengal Ayushman Bharat deadline 2026"]
    flagged_sources = {s for f in wb.findings if f.kind == "staleness" for s in f.source_ids}
    domains = {s.registrable_domain for s in wb.sources if s.id in flagged_sources}
    stale = [f for f in wb.findings if f.kind == "staleness"]
    assert len(stale) == 2 and len(flagged_sources) == 2 and domains == {"instagram.com"}
