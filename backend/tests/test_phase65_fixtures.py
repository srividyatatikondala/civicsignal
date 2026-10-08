"""
Phase 6.5 on the REAL captures (base 2026-10-01, follow-ups 2026-10-05). Mock mode, no new searches.
"""

import asyncio
from datetime import date

import pytest

from civicsignal.config import BACKEND_DIR, Settings
from civicsignal.db import Repository
from civicsignal.models import InvestigateRequest, SearchReason
from civicsignal.orchestrator import Investigator
from civicsignal.report import build_report
from civicsignal.serp import SerpApiClient

FIXTURES = BACKEND_DIR / "fixtures" / "serpapi"
AADHAAR = "Aadhaar free update last date 2026"


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p65")
    settings = Settings(mock_serpapi=True, fixtures_dir=FIXTURES, cache_enabled=False, database_path=tmp / "db.sqlite3")
    repo = Repository(settings.database_path)
    repo.init_schema()
    inv = Investigator(settings, SerpApiClient(settings), repo, today=lambda: date(2026, 10, 1))
    cache = {}

    def _get(query, follow_ups=True):
        key = (query, follow_ups)
        if key not in cache:
            cache[key] = asyncio.run(inv.investigate(InvestigateRequest(query=query, follow_ups=follow_ups)))
        return cache[key]

    return _get


def test_aadhaar_before_and_after_follow_ups(run):
    before = run(AADHAAR, follow_ups=False)
    after = run(AADHAAR)
    assert before.integrity_status.value == "INSUFFICIENT_EVIDENCE"  # base-only behaviour unchanged
    assert after.integrity_status.value == "MINOR_ISSUES"
    m = after.metrics
    # original search measured on its own 10 results; follow-up evidence kept separate
    assert m.base_results == 10 and m.base_relevance_levels == {"off_topic": 10}
    assert m.follow_up_results == 10 and m.follow_up_new_sources == 10
    assert m.follow_up_relevance_levels == {"high": 4, "medium": 3, "off_topic": 3}
    # three official concepts
    assert m.official_sources == 11 and m.primary_sources == 7
    assert build_report(after).status.explanation.startswith(
        "The original search did not address the question; targeted official-source searches recovered "
        "7 relevant official pages that address the topic."
    )


def test_aadhaar_roles_are_identifiable_and_no_false_conflict(run):
    inv = run(AADHAAR)
    role = {s.id: s.request.reason for s in inv.searches}
    base = [r for r in inv.results if role[r.search_run_id] == SearchReason.BASE_QUERY]
    follow = [r for r in inv.results if role[r.search_run_id] == SearchReason.PRIMARY_SOURCE_LOOKUP]
    assert len(base) == 10 and len(follow) == 10
    official_follow = {r.source_id for r in follow if r.relevance.value in ("high", "medium")}
    assert official_follow  # follow-up official pages are evidence
    assert not [f for f in inv.findings if f.kind == "contradiction"]
    # relevance finding is about the original search only
    (rel,) = [f for f in inv.findings if f.kind == "relevance"]
    assert set(rel.result_ids) <= {r.id for r in base} and rel.total_results == 10


def test_aadhaar_support_is_claim_specific(run):
    inv = run(AADHAAR)
    (lookup,) = inv.follow_ups
    assert lookup.outcome_code == "support_found"
    supported = {v.value for v in inv.official_support if v.official_source_ids}
    unsupported = {v.value for v in inv.official_support if not v.official_source_ids}
    assert supported and supported.isdisjoint(unsupported)
    # an official page being found is not support for every value
    assert len(lookup.values_with_official_support) == len(supported)


def test_pm_kisan_and_pan_and_west_bengal_outcomes(run):
    pm = run("PM Kisan next installment date 2026")
    assert [(f.reason.value, f.outcome_code) for f in pm.follow_ups] == [
        ("primary_source_lookup", "official_found_not_addressing_claims"),
        ("news_check", "additional_relevant_evidence"),
    ]
    assert pm.integrity_status.value == "CONFLICTING"

    pan = run("PAN Aadhaar link last date 2026")
    assert [(f.reason.value, f.outcome_code) for f in pan.follow_ups] == [
        ("primary_source_lookup", "official_found_not_addressing_claims"),
        ("recency_contrast", "newer_relevant_evidence"),
    ]
    assert pan.metrics.base_relevance_levels == {"high": 8, "medium": 4}
    assert not [f for f in pan.findings if f.kind == "relevance"]  # follow-up off-topic no longer counted

    wb = run("West Bengal Ayushman Bharat deadline 2026")
    (lookup,) = [f for f in wb.follow_ups if f.reason.value == "primary_source_lookup"]
    assert lookup.status.value == "empty" and lookup.outcome_code == "no_results"
    assert wb.status.value == "completed"  # empty is not a failure


def test_follow_up_outcome_language_is_hedged(run):
    import re

    banned = re.compile(r"\b(true|false|correct|incorrect|fake|verified)\b", re.I)
    for q in [AADHAAR, "PM Kisan next installment date 2026", "PAN Aadhaar link last date 2026",
              "West Bengal Ayushman Bharat deadline 2026", "Telangana Rythu Bharosa installment 2026"]:
        for f in run(q).follow_ups:
            assert not banned.search(f.summary), f.summary
