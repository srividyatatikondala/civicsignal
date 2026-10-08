"""
Investigation report / Evidence Map: built from the real captures (mock mode,
follow-ups executed) and through the API.
"""

import asyncio
import json
import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from civicsignal.api import create_app
from civicsignal.config import BACKEND_DIR, Settings
from civicsignal.db import Repository
from civicsignal.models import InvestigateRequest
from civicsignal.orchestrator import Investigator
from civicsignal.report import build_report, safe_url
from civicsignal.serp import SerpApiClient

from .conftest import base_fixture_queries

FIXTURES = BACKEND_DIR / "fixtures" / "serpapi"
BANNED = re.compile(r"\b(true|false|fake|incorrect|liar|fraud(?:ulent)?|truth score|verified)\b", re.I)


@pytest.fixture(scope="module")
def reports(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("reports")
    settings = Settings(mock_serpapi=True, fixtures_dir=FIXTURES, cache_enabled=False, database_path=tmp / "db.sqlite3")
    repo = Repository(settings.database_path)
    repo.init_schema()
    inv = Investigator(settings, SerpApiClient(settings), repo, today=lambda: date(2026, 10, 1))
    return {q: build_report(asyncio.run(inv.investigate(InvestigateRequest(query=q))))
            for q in base_fixture_queries(FIXTURES)}


def test_every_report_is_complete_and_labelled_demo(reports):
    assert len(reports) == 8
    for r in reports.values():
        assert r.status.label and r.status.explanation
        assert r.data_notice.label == "CAPTURED SERPAPI DATA" and "not a live search" in r.data_notice.detail
        assert r.method_notes and r.where_to_verify_note
        L = r.landscape
        assert L.result_appearances >= L.unique_urls >= L.unique_sources
        assert L.unique_sources >= (L.independent_source_groups or 0)


def test_every_issue_has_evidence_with_safe_urls(reports):
    for r in reports.values():
        for card in r.issues:
            assert card.evidence, (r.question, card.title)
            for e in card.evidence:
                assert e.url is None or e.url.startswith(("http://", "https://"))
                assert e.found_by


def test_text_evidence_quotes_are_exact(reports):
    """Quotes in staleness/conflict cards are verbatim substrings of a retrieved title/snippet."""
    raw_text = []
    for path in FIXTURES.glob("*.json"):
        resp = json.loads(path.read_text(encoding="utf-8"))["response"]
        for block in ("organic_results", "news_results", "top_stories", "related_questions"):
            for item in resp.get(block) or []:
                if isinstance(item, dict):
                    raw_text += [item.get("title") or "", item.get("snippet") or "", item.get("question") or ""]
                    for s in item.get("stories") or []:
                        raw_text += [s.get("title") or "", s.get("snippet") or ""]
    for r in reports.values():
        for card in r.issues:
            if card.category in ("staleness", "conflicting_claims"):
                for e in card.evidence:
                    assert any(e.quote in t for t in raw_text), e.quote


def test_report_language_is_hedged(reports):
    for r in reports.values():
        text = " ".join(
            [r.status.label, r.status.explanation, r.where_to_verify_note]
            + [c.title + " " + c.summary for c in r.issues]
            + [f.outcome for f in r.follow_ups]
        )
        hits = [m.group(0) for m in BANNED.finditer(text)]
        assert not hits, (r.question, hits)


def test_pm_kisan_evidence_map(reports):
    r = reports["PM Kisan next installment date 2026"]
    assert r.status.code == "CONFLICTING"
    top = r.evidence_map[0]  # conflicts first
    assert top.has_conflict and top.attribute == "expected date" and top.subject == "installment 23"
    assert [v.value for v in top.values] == ["2026-06-20", "2026-07-01/2026-07-31"]
    assert [sorted(s.domain for s in v.sources) for v in top.values] == [["governmentjobhub.com"],
                                                                        ["dailyhunt.in", "latestly.com"]]
    assert all(not v.stated_by_official_source for v in top.values)
    domains = [v.domain for v in r.where_to_verify]
    assert "pmkisan.gov.in" in domains and "jnu.ac.in" not in domains  # faculty page named "Kisan" excluded
    (lookup,) = [f for f in r.follow_ups if f.reason == "primary_source_lookup"]
    assert lookup.reason_label == "Look for the official source"
    assert lookup.outcome_code == "official_found_not_addressing_claims"
    assert lookup.outcome.startswith("Official pages addressing the topic were found")
    assert "do not state any of the claim values" in lookup.outcome


def test_issue_cards_ordered_conflicts_first(reports):
    r = reports["PAN Aadhaar link last date 2026"]
    assert r.issues[0].category == "conflicting_claims"
    assert r.issue_counts["staleness"] >= 3


def test_no_official_source_gives_honest_note(reports):
    r = reports["Telangana Rythu Bharosa installment 2026"]
    assert r.where_to_verify == []
    assert r.where_to_verify_note.startswith("No official page addressing this topic was found")


def test_clean_control_report(reports):
    r = reports["RTI application fee central government"]
    assert r.status.code == "NOT_ASSESSED"
    assert r.status.label == "No issues detected by the checks run"
    assert "not a verification" in r.status.explanation
    assert r.issues == [] and r.follow_ups == []
    fee = next(m for m in r.evidence_map if m.attribute == "fee")
    assert not fee.has_conflict and fee.values[0].value == "₹10" and len(fee.values[0].sources) == 2


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://uidai.gov.in/x", "https://uidai.gov.in/x"),
        ("http://a.in", "http://a.in"),
        ("javascript:alert(1)", None),
        ("data:text/html,<script>", None),
        ("ftp://a.in/x", None),
        ("", None),
        (None, None),
    ],
)
def test_safe_url(url, expected):
    assert safe_url(url) == expected


def test_report_endpoint(tmp_settings):
    settings = tmp_settings.model_copy(update={"mock_serpapi": True, "serpapi_api_key": None, "fixtures_dir": FIXTURES})
    repo = Repository(settings.database_path)
    client = SerpApiClient(settings)
    inv = Investigator(settings, client, repo, today=lambda: date(2026, 10, 1))
    api = TestClient(create_app(settings, client=client, repository=repo, investigator=inv))
    created = api.post("/api/investigate", json={"query": "PM Kisan next installment date 2026"}).json()
    rep = api.get(f"/api/investigations/{created['id']}/report")
    assert rep.status_code == 200
    body = rep.json()
    assert body["question"] == "PM Kisan next installment date 2026"
    assert body["status"]["code"] == "CONFLICTING" and body["data_notice"]["label"] == "CAPTURED SERPAPI DATA"
    assert body["evidence_map"] and body["issues"] and body["follow_ups"]
    assert api.get("/api/investigations/inv_missing/report").status_code == 404

    demo = api.get("/api/demo-queries").json()
    assert demo["mock_mode"] is True
    assert len(demo["queries"]) == 8  # base captures only, no follow-up captures
    assert "PM Kisan next installment date 2026" in {q["query"] for q in demo["queries"]}


def test_demo_queries_empty_in_live_mode(tmp_settings):
    api = TestClient(create_app(tmp_settings))
    assert api.get("/api/demo-queries").json() == {"mock_mode": False, "queries": []}


def test_root_explains_the_service(tmp_settings):
    body = TestClient(create_app(tmp_settings)).get("/").json()
    assert body["service"] == "CivicSignal API" and body["mode"].startswith("live")
    assert body["dashboard"] == "http://localhost:5173"
