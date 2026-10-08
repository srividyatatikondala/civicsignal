"""
Duplication / independence regression tests on the 8 REAL SerpApi captures (2026-10-01).

Result (thresholds fixed before evaluation): no textual duplication between
sources in these captures; only same-publisher relationships. Two plausible
relationships fall below the thresholds and are recorded as strict-xfail
known limitations, not tuned away.
"""

import asyncio
import json
from datetime import date
from pathlib import Path

import pytest

from civicsignal.config import BACKEND_DIR, Settings
from civicsignal.db import Repository
from civicsignal.models import InvestigateRequest
from civicsignal.orchestrator import Investigator
from civicsignal.serp import SerpApiClient

from .conftest import base_fixture_queries

FIXTURES = BACKEND_DIR / "fixtures" / "serpapi"

# query -> (unique_sources, independent_source_groups, sorted same-publisher domain pairs)
EXPECTED = {
    "Aadhaar free update last date 2026": (9, 8, [("play.google.com", "support.google.com")]),
    "Indian passport validity for adults": (
        8, 6,
        [("passportindia.gov.in", "mportal.passportindia.gov.in"),
         ("passportindia.gov.in", "mportal.passportindia.gov.in"),
         ("passportindia.gov.in", "passportindia.gov.in")],
    ),
    "NEET UG 2026 exam date": (
        13, 11, [("medadmit.in", "medadmit.in"), ("news.careers360.com", "medicine.careers360.com")]
    ),
    "PAN Aadhaar link last date 2026": (8, 8, []),
    "PM Kisan next installment date 2026": (8, 8, []),
    "RTI application fee central government": (8, 8, []),
    "Telangana Rythu Bharosa installment 2026": (8, 8, []),  # two x.com posts: platform, not one publisher
    "West Bengal Ayushman Bharat deadline 2026": (9, 9, []),  # two instagram posts: platform
}


@pytest.fixture(scope="module")
def investigations(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("duplication_fixtures")
    settings = Settings(mock_serpapi=True, fixtures_dir=FIXTURES, cache_enabled=False, database_path=tmp / "db.sqlite3")
    repo = Repository(settings.database_path)
    repo.init_schema()
    inv = Investigator(settings, SerpApiClient(settings), repo, today=lambda: date(2026, 10, 1))
    out = {}
    for q in base_fixture_queries(FIXTURES):
        # Phase 2-5 snapshot: analysis of the BASE search only (follow-ups have their own snapshot)
        out[q] = asyncio.run(inv.investigate(InvestigateRequest(query=q, follow_ups=False)))
    return out


@pytest.mark.parametrize("query", sorted(EXPECTED))
def test_independence_snapshot(investigations, query):
    inv = investigations[query]
    sources, groups, pairs = EXPECTED[query]
    domains = {s.id: s.domain for s in inv.sources}
    assert inv.metrics.unique_sources == sources
    assert inv.metrics.independent_source_groups == groups
    assert inv.metrics.apparent_duplicate_groups == 0
    rels = inv.independence.relationships
    assert {r.kind.value for r in rels} <= {"same_publisher"}  # no textual duplication found
    assert sorted((domains[r.source_a_id], domains[r.source_b_id]) for r in rels) == sorted(pairs)


def test_no_duplication_findings_and_status_unchanged(investigations):
    expected_status = {
        "PAN Aadhaar link last date 2026": "CONFLICTING",
        "PM Kisan next installment date 2026": "CONFLICTING",
        "Telangana Rythu Bharosa installment 2026": "POTENTIALLY_STALE",
        "West Bengal Ayushman Bharat deadline 2026": "POTENTIALLY_STALE",
        "Aadhaar free update last date 2026": "INSUFFICIENT_EVIDENCE",  # all results off-topic (Phase 5)
        "NEET UG 2026 exam date": "MINOR_ISSUES",  # one off-topic result (Phase 5)
    }
    for query, inv in investigations.items():
        assert [f for f in inv.findings if f.kind == "duplication"] == []
        assert inv.integrity_status.value == expected_status.get(query, "NOT_ASSESSED")


def test_counts_are_separate_and_ordered(investigations):
    for inv in investigations.values():
        m = inv.metrics
        assert m.total_search_results >= m.unique_urls >= m.unique_sources >= m.independent_source_groups


def _pair_relation(inv, domain_a, domain_b):
    domains = {s.id: s.domain for s in inv.sources}
    for r in inv.independence.relationships:
        if {domains[r.source_a_id], domains[r.source_b_id]} == {domain_a, domain_b}:
            return r
    return None


@pytest.mark.xfail(strict=True, reason="KNOWN LIMITATION: likely wire story; headline overlap 0.67 < 0.70 threshold")
def test_known_limitation_rti_wire_story_headlines(investigations):
    inv = investigations["RTI application fee central government"]
    assert _pair_relation(inv, "morungexpress.com", "scroll.in") is not None


@pytest.mark.xfail(strict=True, reason="KNOWN LIMITATION: identical sentence made only of generic words + date stays 'uncertain'")
def test_known_limitation_instagram_identical_generic_sentence(investigations):
    inv = investigations["West Bengal Ayushman Bharat deadline 2026"]
    domains = {s.id: s.domain for s in inv.sources}
    insta = [s.id for s in inv.sources if domains[s.id] == "instagram.com"]
    assert len(insta) == 2
    linked = any({r.source_a_id, r.source_b_id} == set(insta) for r in inv.independence.relationships)
    assert linked
