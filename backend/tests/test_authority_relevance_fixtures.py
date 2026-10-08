"""
Authority / relevance regression snapshot on the 8 REAL SerpApi captures (2026-10-01).
Expected values come from manual review of each source and result; not a benchmark.
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

# query -> (status, primary_sources, authority finding title or None, relevance finding level or None)
EXPECTED = {
    "Aadhaar free update last date 2026": ("INSUFFICIENT_EVIDENCE", 0, "Limited primary-source coverage", "off_topic"),
    "PM Kisan next installment date 2026": ("CONFLICTING", 0, "Limited primary-source coverage", "low"),  # "CM Kisan" PAA
    "Telangana Rythu Bharosa installment 2026": ("POTENTIALLY_STALE", 0, "Limited primary-source coverage", "low"),
    "West Bengal Ayushman Bharat deadline 2026": ("POTENTIALLY_STALE", 0, "Limited primary-source coverage", "low"),
    "PAN Aadhaar link last date 2026": ("CONFLICTING", 1, "Few primary sources among results", None),
    # clean controls
    "RTI application fee central government": ("NOT_ASSESSED", 2, None, None),
    "Indian passport validity for adults": ("NOT_ASSESSED", 5, None, None),
    # 2026-10-05: generic "UG admissions" pages no longer count as NEET-relevant ("ug" alone is not a match);
    # official topic sources are now mcc.nic.in, dme.assam.gov.in, cgdme.admissions.nic.in
    "NEET UG 2026 exam date": ("MINOR_ISSUES", 3, "Few primary sources among results", "off_topic"),  # 3 of 13
}


@pytest.fixture(scope="module")
def investigations(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("p5_fixtures")
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
def test_snapshot(investigations, query):
    inv = investigations[query]
    status, primary, authority_title, relevance_level = EXPECTED[query]
    assert inv.integrity_status.value == status
    assert inv.metrics.primary_sources == primary
    auth = [f.title for f in inv.findings if f.kind == "authority"]
    assert auth == ([authority_title] if authority_title else [])
    rel = [f.level for f in inv.findings if f.kind == "relevance"]
    assert rel == ([relevance_level] if relevance_level else [])


def test_every_source_and_result_is_labelled_with_reasons(investigations):
    for inv in investigations.values():
        assert all(s.type_signals for s in inv.sources)
        assert all(r.relevance is not None and r.relevance_signals for r in inv.results)
        assert sum(inv.metrics.source_types.values()) == inv.metrics.unique_sources


def test_off_topic_aadhaar_results_are_all_off_topic(investigations):
    inv = investigations["Aadhaar free update last date 2026"]
    assert inv.metrics.relevance_levels == {"off_topic": 10}


def test_official_but_off_topic_page_is_not_a_primary_source(investigations):
    inv = investigations["Aadhaar free update last date 2026"]
    (finhry,) = [s for s in inv.sources if s.domain == "finhry.gov.in"]
    assert finhry.source_type.value == "official" and inv.metrics.primary_sources == 0


def test_pm_kisan_conflicting_values_have_no_official_backing(investigations):
    inv = investigations["PM Kisan next installment date 2026"]
    expected = [v for v in inv.official_support if v.attribute == "expected_date"]
    assert expected and all(v.official_source_ids == [] for v in expected)


def test_known_limitation_homonym_rated_relevant(investigations):
    """Lexical relevance cannot see that 'Passport' (Euromonitor's market-research product) is a different thing."""
    inv = investigations["Indian passport validity for adults"]
    (euro,) = [r for r in inv.results if r.domain == "euromonitor.com"]
    assert euro.relevance.value == "medium"  # documented limitation, not desired behaviour
