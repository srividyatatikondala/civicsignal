"""
Contradiction regression tests on the 8 REAL SerpApi captures (2026-10-01).

Expected conflicts were set by manual reading of the snippets. This is a
regression snapshot of current behaviour, not a labelled benchmark.
No rule in the detector refers to any site in these fixtures.
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

CAPTURE_DATE = date(2026, 10, 1)
FIXTURES = BACKEND_DIR / "fixtures" / "serpapi"

# query -> list of (scope, subject, attribute, conflicting_values)
EXPECTED_CONFLICTS = {
    # 23rd installment: one source says "will be released on 20 June 2026",
    # two say "expected around July 2026".
    "PM Kisan next installment date 2026": [
        ("cross_source", "installment:23", "expected_date", ["2026-06-20", "2026-07-01/2026-07-31"]),
    ],
    # "December 31, 2019 is the last date" vs "December 31, 2025 as the deadline" (x2).
    # One of the 2025 sources (allindiaitr) is historical narration the rules cannot detect
    # (see test_staleness_fixtures.py); the conflict exists without it as well.
    "PAN Aadhaar link last date 2026": [
        ("cross_source", "", "deadline", ["2019-12-31", "2025-12-31"]),
    ],
    "Aadhaar free update last date 2026": [],
    "NEET UG 2026 exam date": [],
    "RTI application fee central government": [],  # two sources agree on ₹10
    "Indian passport validity for adults": [],
    "Telangana Rythu Bharosa installment 2026": [],  # crore totals differ but are never compared
    "West Bengal Ayushman Bharat deadline 2026": [],  # two sources state the same deadline
}


@pytest.fixture(scope="module")
def investigations(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("contradiction_fixtures")
    settings = Settings(mock_serpapi=True, fixtures_dir=FIXTURES, cache_enabled=False, database_path=tmp / "db.sqlite3")
    repo = Repository(settings.database_path)
    repo.init_schema()
    inv = Investigator(settings, SerpApiClient(settings), repo, today=lambda: CAPTURE_DATE)
    out = {}
    for q in base_fixture_queries(FIXTURES):
        # Phase 2-5 snapshot: analysis of the BASE search only (follow-ups have their own snapshot)
        out[q] = asyncio.run(inv.investigate(InvestigateRequest(query=q, follow_ups=False)))
    return out


@pytest.mark.parametrize("query", sorted(EXPECTED_CONFLICTS))
def test_conflicts_match_snapshot(investigations, query):
    got = [
        (f.scope.value, f.subject, f.attribute, f.conflicting_values)
        for f in investigations[query].findings
        if f.kind == "contradiction"
    ]
    assert got == EXPECTED_CONFLICTS[query]


def test_conflicting_status_only_where_conflicts_exist(investigations):
    for query, expected in EXPECTED_CONFLICTS.items():
        status = investigations[query].integrity_status.value
        assert (status == "CONFLICTING") == bool(expected), query


def test_rti_fee_agreement_is_a_cluster_without_conflict(investigations):
    inv = investigations["RTI application fee central government"]
    (fee,) = [c for c in inv.clusters if c.attribute == "fee"]
    assert not fee.has_conflict and len(fee.source_ids) == 2
    assert [g.value for g in fee.value_groups] == ["₹10"]


def test_questions_from_people_also_ask_are_not_claims(investigations):
    for inv in investigations.values():
        for c in inv.claims:
            assert not c.claim_text.rstrip().endswith("?")


def test_pm_kisan_conflict_preserves_every_source_and_evidence(investigations):
    inv = investigations["PM Kisan next installment date 2026"]
    (f,) = [f for f in inv.findings if f.kind == "contradiction"]
    results = {r.id: r for r in inv.results}
    spans = {e.id: e for e in inv.evidence_spans}
    domains = sorted(results[rid].domain for rid in f.result_ids)
    assert domains == ["dailyhunt.in", "governmentjobhub.com", "latestly.com"]
    assert [len(g.source_ids) for g in f.value_groups] == [1, 2]
    for eid in f.evidence_span_ids:
        e = spans[eid]
        assert results[e.result_id].snippet[e.start : e.end] == e.text
        assert "23rd" in e.text


def test_scheme_level_amount_is_not_scoped_to_title_installment(investigations):
    inv = investigations["PM Kisan next installment date 2026"]
    (annual,) = [c for c in inv.claims if c.attribute == "annual_benefit"]
    assert annual.subject == ""
