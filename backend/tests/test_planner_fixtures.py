"""
Planner decisions and executed follow-ups on the REAL SerpApi captures, mock mode.

Base searches were captured 2026-10-01; the 9 planned follow-ups were captured
2026-10-05 (capture_followups --execute). The planning decisions are computed
from the base search alone, so they are unchanged by the follow-up captures.
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

EXPECTED = {
    "Aadhaar free update last date 2026": [
        ("primary_source_lookup", "google", "Aadhaar free update last date 2026 (site:gov.in OR site:nic.in)", None),
    ],
    "PM Kisan next installment date 2026": [
        ("primary_source_lookup", "google", "PM Kisan 23rd installment date 2026 (site:gov.in OR site:nic.in)", None),
        ("news_check", "google_news", "PM Kisan next installment date 2026", None),
    ],
    "PAN Aadhaar link last date 2026": [
        ("primary_source_lookup", "google", "PAN Aadhaar link last date 2026 site:incometax.gov.in", None),
        ("recency_contrast", "google", "PAN Aadhaar link last date 2026", "qdr:m"),
    ],
    "Telangana Rythu Bharosa installment 2026": [
        ("primary_source_lookup", "google", "Telangana Rythu Bharosa installment 2026 (site:gov.in OR site:nic.in)", None),
        ("recency_contrast", "google", "Telangana Rythu Bharosa installment 2026", "qdr:m"),
    ],
    "West Bengal Ayushman Bharat deadline 2026": [
        ("primary_source_lookup", "google", "West Bengal Ayushman Bharat deadline 2026 site:pmjay.gov.in", None),
        ("recency_contrast", "google", "West Bengal Ayushman Bharat deadline 2026", "qdr:m"),
    ],
    # clean controls: relevant official sources present, no conflicts, nothing stale -> no searches
    "RTI application fee central government": [],
    "Indian passport validity for adults": [],
    "NEET UG 2026 exam date": [],
}


@pytest.fixture(scope="module")
def investigations(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("planner_fixtures")
    settings = Settings(mock_serpapi=True, fixtures_dir=FIXTURES, cache_enabled=False, database_path=tmp / "db.sqlite3")
    repo = Repository(settings.database_path)
    repo.init_schema()
    inv = Investigator(settings, SerpApiClient(settings), repo, today=lambda: date(2026, 10, 1))
    out = {}
    for q in base_fixture_queries(FIXTURES):
        out[q] = asyncio.run(inv.investigate(InvestigateRequest(query=q)))
    return out


@pytest.mark.parametrize("query", sorted(EXPECTED))
def test_planner_decisions(investigations, query):
    inv = investigations[query]
    got = [(f.reason.value, f.engine, f.query, f.params.get("tbs")) for f in inv.follow_ups]
    assert got == EXPECTED[query]
    assert len(inv.searches) <= 3  # 1 base + at most 2 follow-ups


def test_every_follow_up_is_explained_executed_and_traceable(investigations):
    for inv in investigations.values():
        runs = {s.id: s for s in inv.searches}
        for f in inv.follow_ups:
            assert f.status.value in ("ok", "empty")
            assert f.question and f.triggered_by
            run = runs[f.search_run_id]
            assert run.parent_search_id == inv.searches[0].id and run.planned_at and run.fetched_at
        assert inv.status.value == "completed"


def test_ranked_results_are_capped_per_search(investigations):
    """google_news returned 100 stories for the PM Kisan news check; only 10 enter the evidence."""
    from collections import Counter

    for inv in investigations.values():
        per_block = Counter((r.search_run_id, r.result_type.value) for r in inv.results
                            if r.result_type.value in ("organic", "news", "top_story"))
        assert max(per_block.values()) <= 10
    news = [f for f in investigations["PM Kisan next installment date 2026"].follow_ups if f.reason.value == "news_check"]
    assert news and news[0].results_added == 10


# Outcomes of the executed follow-ups (evidence statements, not verdicts):
# query -> (outcome_code, official page addressing the topic found, claim value supported, expected official domain)
PRIMARY_OUTCOMES = {
    # official page found, but it states none of the claim values in the results
    "PM Kisan next installment date 2026": ("official_found_not_addressing_claims", True, False, "pmkisan.gov.in"),
    "PAN Aadhaar link last date 2026": ("official_found_not_addressing_claims", True, False, "incometaxindia.gov.in"),
    # official pages state specific values (deadline Dec 2026, fee ₹75)
    "Aadhaar free update last date 2026": ("support_found", True, True, "uidai.gov.in"),
    # 11 official pages returned, none addresses the topic
    "Telangana Rythu Bharosa installment 2026": ("no_useful_official_result", False, False, None),
    # site:pmjay.gov.in returned nothing (empty, not failed)
    "West Bengal Ayushman Bharat deadline 2026": ("no_results", False, False, None),
}


@pytest.mark.parametrize("query", sorted(PRIMARY_OUTCOMES))
def test_primary_source_lookup_outcomes(investigations, query):
    code, topic_pages, supported, domain = PRIMARY_OUTCOMES[query]
    inv = investigations[query]
    (outcome,) = [f for f in inv.follow_ups if f.reason.value == "primary_source_lookup"]
    assert outcome.outcome_code == code
    assert outcome.official_topic_pages_found is topic_pages
    assert outcome.primary_source_support_found is supported
    if domain:
        sources = {s.id: s.domain for s in inv.sources}
        assert domain in {sources[s] for s in outcome.official_relevant_source_ids}


def test_pm_kisan_official_source_does_not_address_the_conflicting_dates(investigations):
    inv = investigations["PM Kisan next installment date 2026"]
    (outcome,) = [f for f in inv.follow_ups if f.reason.value == "primary_source_lookup"]
    assert "expected date 2026-06-20" in outcome.values_not_addressed_by_official_sources
    assert any(f.kind == "contradiction" for f in inv.findings)  # still reported, not settled by decree


# --- Regression: issues found once follow-ups ran on real data (2026-10-05), fixed the same day. ---


def _stale_domains(inv):
    results = {r.id: r for r in inv.results}
    return {results[f.result_ids[0]].domain for f in inv.findings if f.kind == "staleness"}


def test_off_topic_result_produces_no_staleness(investigations):
    """Was: off-topic gem.gov.in page produced a 'stale deadline'."""
    assert "gem.gov.in" not in _stale_domains(investigations["Telangana Rythu Bharosa installment 2026"])


def test_procurement_notice_creates_no_contradiction(investigations):
    """Was: UIDAI 'Amendments to RFP' bid dates (25 vs 26 July) became an 'internal contradiction'."""
    inv = investigations["Aadhaar free update last date 2026"]
    assert not [f for f in inv.findings if f.kind == "contradiction"]
    rfp = [c for c in inv.claims if c.eligibility.value == "procurement_notice"]
    assert rfp and all(not c.comparable for c in rfp)  # kept as evidence


def test_bank_tender_is_not_a_stale_pan_deadline(investigations):
    """Was: a bank lease tender mentioning 'PAN' produced 'stale PAN deadline' findings."""
    assert "bankofbaroda.bank.in" not in _stale_domains(investigations["PAN Aadhaar link last date 2026"])


def test_west_bengal_lookup_targets_domain_mentioned_in_evidence(investigations):
    (lookup,) = [f for f in investigations["West Bengal Ayushman Bharat deadline 2026"].follow_ups
                 if f.reason.value == "primary_source_lookup"]
    assert any("pmjay.gov.in is mentioned in result #5 (instagram.com)" in t for t in lookup.triggered_by)
