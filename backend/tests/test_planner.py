"""
Phase 6: follow-up search planner. Full pipeline with a scripted SerpApi
(responses depend on the request), no network.
"""

import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from civicsignal import orchestrator
from civicsignal.config import BACKEND_DIR
from civicsignal.db import Repository
from civicsignal.models import InvestigateRequest
from civicsignal.orchestrator import Investigator
from civicsignal.serp import SerpApiClient

pytestmark = pytest.mark.anyio

TOPIC = "Sample Yojana next installment date 2026"


def org(title, link, snippet):
    return (title, link, snippet)


def _body(*organic, news=False):
    key = "news_results" if news else "organic_results"
    return {
        "search_metadata": {"id": "x", "status": "Success"},
        key: [{"position": i + 1, "title": t, "link": l, "snippet": s} for i, (t, l, s) in enumerate(organic)],
    }


class Router:
    """Scripted SerpApi: picks a response by engine / site: filter / tbs, and records every request."""

    def __init__(self, base, primary=None, news=None, recency=None):
        self.base, self.primary, self.news, self.recency = base, primary, news, recency
        self.requests: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        if params.get("engine") == "google_news":
            body = self.news or _body(news=True)
        elif "site:" in params.get("q", ""):
            body = self.primary or _body()
        elif params.get("tbs"):
            body = self.recency or _body()
        else:
            body = self.base
        return httpx.Response(200, json=body)


async def _run(tmp_settings, router, query=TOPIC, **settings_update):
    settings = tmp_settings.model_copy(update=settings_update)
    repo = Repository(settings.database_path)
    repo.init_schema()

    async def no_sleep(_):
        return None

    client = SerpApiClient(settings, transport=httpx.MockTransport(router), sleep=no_sleep)
    inv = Investigator(settings, client, repo, today=lambda: date(2026, 10, 1))
    return await inv.investigate(InvestigateRequest(query=query)), repo


BLOG_A = org("Sample Yojana 23rd installment date 2026", "https://blog-a.example.com/sample-yojana",
             "The Sample Yojana 23rd installment will be released on 20 June 2026.")
BLOG_B = org("Sample Yojana 23rd installment update", "https://news-b.example.org/sample-yojana",
             "The 23rd installment of Sample Yojana is expected around July 2026.")
OFFICIAL_RELEVANT = org("Sample Yojana - official portal", "https://sampleyojana.gov.in/installments",
                        "Sample Yojana 23rd installment is scheduled for 20 June 2026.")
OFFICIAL_NO_DATES = org("Sample Yojana - official portal", "https://sampleyojana.gov.in/",
                        "Sample Yojana installment information and beneficiary status.")
OFFICIAL_OFF_TOPIC = org("Last Pay Certificate rules", "https://finance.state.gov.in/last-pay",
                         "Treasury rules amendment for pay certificates.")
NO_DATES = org("Sample Yojana beneficiary list", "https://portal-c.example.net/sample-yojana",
               "Check the Sample Yojana beneficiary list and payment status.")


def _reasons(inv):
    return [s.request.reason.value for s in inv.searches]


# 1 + 4 + 5 ---------------------------------------------------------------------------------


async def test_no_primary_source_triggers_lookup_and_found_official_enters_evidence(tmp_settings):
    router = Router(base=_body(BLOG_A, BLOG_B), primary=_body(OFFICIAL_RELEVANT))
    inv, repo = await _run(tmp_settings, router)
    assert _reasons(inv)[:2] == ["base_query", "primary_source_lookup"]
    lookup = next(s for s in inv.searches if s.request.reason.value == "primary_source_lookup")
    # sharpened by the conflict qualifier; generic official filter (no domain known yet)
    assert lookup.request.q == "Sample Yojana 23rd installment date 2026 (site:gov.in OR site:nic.in)"
    assert lookup.parent_search_id == inv.searches[0].id and lookup.planned_at is not None
    assert lookup.question and any("none of these values is stated by an official source" in t
                                   for t in lookup.triggered_by)
    assert any("No official page that addresses the topic" in t for t in lookup.triggered_by)

    # the official page found by the follow-up joins the same evidence model
    official = [s for s in inv.sources if s.domain == "sampleyojana.gov.in"]
    assert len(official) == 1 and official[0].source_type.value == "official"
    assert inv.metrics.primary_sources == 1
    assert any(c.source_id == official[0].id for c in inv.claims)
    outcome = next(f for f in inv.follow_ups if f.reason.value == "primary_source_lookup")
    assert outcome.outcome_code == "support_found"
    assert outcome.official_topic_pages_found is True and outcome.primary_source_support_found is True
    assert outcome.official_relevant_source_ids == [official[0].id]
    assert "expected date 2026-06-20" in outcome.values_with_official_support
    assert "expected date 2026-07-01/2026-07-31" in outcome.values_not_addressed_by_official_sources
    assert outcome.summary.startswith("Official source support found")
    for banned in ("true", "false", "correct", "incorrect"):
        assert banned not in outcome.summary.lower()
    # the conflict is still reported — official support does not settle it by decree
    assert any(f.kind == "contradiction" for f in inv.findings)
    # raw response of the follow-up is preserved
    assert repo.get_raw_response(inv.id, lookup.id)["organic_results"][0]["link"].startswith("https://sampleyojana")


async def test_primary_lookup_that_finds_nothing_says_no(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(BLOG_A, BLOG_B), primary=_body()))
    outcome = next(f for f in inv.follow_ups if f.reason.value == "primary_source_lookup")
    assert outcome.outcome_code == "no_results"
    assert outcome.primary_source_support_found is False and outcome.official_topic_pages_found is False
    assert outcome.summary == "The search returned no results."


# 2 ------------------------------------------------------------------------------------------


async def test_relevant_official_source_present_means_no_lookup(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(OFFICIAL_NO_DATES, NO_DATES)))
    assert _reasons(inv) == ["base_query"]
    assert inv.follow_ups == []


# 3 ------------------------------------------------------------------------------------------


async def test_off_topic_official_source_does_not_count(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(OFFICIAL_OFF_TOPIC, NO_DATES)))
    assert "primary_source_lookup" in _reasons(inv)
    lookup = next(s for s in inv.searches if s.request.reason.value == "primary_source_lookup")
    assert any("No official page that addresses the topic" in t for t in lookup.triggered_by)


# 4b: conflict with an official source present but not backing any value -> targeted lookup


async def test_unbacked_conflict_targets_the_official_domain_already_present(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(OFFICIAL_NO_DATES, BLOG_A, BLOG_B)))
    lookup = next(s for s in inv.searches if s.request.reason.value == "primary_source_lookup")
    assert lookup.request.q == "Sample Yojana 23rd installment date 2026 site:sampleyojana.gov.in"
    assert any("Relevant official source(s) already present" in t for t in lookup.triggered_by)


async def test_official_domain_mentioned_in_relevant_text_is_targeted(tmp_settings):
    mention = org("Sample Yojana guide", "https://guide.example.com/sample-yojana",
                  "Sample Yojana installment details are on https://sampleyojana.gov.in for 2026.")
    inv, _ = await _run(tmp_settings, Router(base=_body(mention, NO_DATES)))
    lookup = next(s for s in inv.searches if s.request.reason.value == "primary_source_lookup")
    assert lookup.request.q.endswith("site:sampleyojana.gov.in")
    assert any("sampleyojana.gov.in is mentioned in result #1" in t for t in lookup.triggered_by)


# NEWS_CHECK / RECENCY_CONTRAST selection ---------------------------------------------------


async def test_passed_expected_date_selects_news_check_not_recency(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(BLOG_A, BLOG_B)))
    assert _reasons(inv) == ["base_query", "primary_source_lookup", "news_check"]
    news = next(s for s in inv.searches if s.request.reason.value == "news_check")
    assert news.request.engine == "google_news"
    assert all("Expected date already passed" in t for t in news.triggered_by)


async def test_passed_deadline_selects_recency_contrast(tmp_settings):
    stale = org("Sample Yojana last date", "https://blog-a.example.com/sample-yojana-deadline",
                "The Sample Yojana last date to apply is 30 June 2026.")
    inv, _ = await _run(tmp_settings, Router(base=_body(stale, OFFICIAL_NO_DATES)))
    assert _reasons(inv) == ["base_query", "recency_contrast"]  # official source present: no lookup
    recency = inv.searches[1]
    assert recency.request.params["tbs"] == "qdr:m"
    assert recency.triggered_by[0].startswith("Deadline already passed: blog-a.example.com")


async def test_regional_compare_only_when_requested(tmp_settings):
    router = Router(base=_body(OFFICIAL_NO_DATES, NO_DATES))
    settings = tmp_settings
    repo = Repository(settings.database_path)
    repo.init_schema()
    client = SerpApiClient(settings, transport=httpx.MockTransport(router))
    inv = await Investigator(settings, client, repo, today=lambda: date(2026, 10, 1)).investigate(
        InvestigateRequest(query=TOPIC, compare_hl="te"))
    assert _reasons(inv) == ["base_query", "regional_compare"]
    assert inv.searches[1].request.params["hl"] == "te"


# 6: limits ------------------------------------------------------------------------------------


async def test_follow_up_budget_is_respected(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(BLOG_A, BLOG_B)), max_follow_up_searches=1)
    assert _reasons(inv) == ["base_query", "primary_source_lookup"]
    inv, _ = await _run(tmp_settings, Router(base=_body(BLOG_A, BLOG_B)), max_searches_per_investigation=1)
    assert _reasons(inv) == ["base_query"]


async def test_planner_can_be_disabled(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(BLOG_A, BLOG_B)), planner_enabled=False)
    assert _reasons(inv) == ["base_query"]


# 7: failure isolation -------------------------------------------------------------------------


async def test_planner_failure_does_not_crash_investigation(tmp_settings, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("planner crashed")

    monkeypatch.setattr(orchestrator, "plan_follow_ups", boom)
    inv, _ = await _run(tmp_settings, Router(base=_body(BLOG_A, BLOG_B)))
    assert _reasons(inv) == ["base_query"] and inv.status.value == "completed"
    assert any("planner was unavailable" in w for w in inv.warnings)
    assert any(f.kind == "contradiction" for f in inv.findings)  # first-pass analysis kept


async def test_failed_follow_up_search_is_partial_not_fatal(tmp_settings):
    def handler(request):
        if "site:" in request.url.params.get("q", ""):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_body(BLOG_A, BLOG_B))

    inv, _ = await _run(tmp_settings, handler, serpapi_max_retries=0)
    assert inv.status.value == "partial"
    assert any(f.kind == "contradiction" for f in inv.findings)


# 8: mock mode — planned, recorded, not executed ------------------------------------------------


async def test_mock_mode_records_planned_follow_ups_without_spending(tmp_settings, repo_fixtures_dir):
    settings = tmp_settings.model_copy(update={"mock_serpapi": True, "serpapi_api_key": None,
                                               "fixtures_dir": repo_fixtures_dir})
    repo = Repository(settings.database_path)
    repo.init_schema()
    inv = await Investigator(settings, SerpApiClient(settings), repo, today=lambda: date(2026, 9, 29)).investigate(
        InvestigateRequest(query="Aadhaar free update last date 2026"))
    skipped = [s for s in inv.searches if s.status.value == "skipped"]
    assert skipped and inv.status.value == "completed"
    assert all(s.question and s.triggered_by and s.parent_search_id for s in skipped)
    assert all(f.primary_source_support_found is None for f in inv.follow_ups)


# 9: no hard-coded topic / site logic ------------------------------------------------------------


def test_planner_has_no_topic_or_site_specific_logic():
    banned = re.compile(r"kisan|aadhaar|pmjay|uidai|incometax|rythu|bengal|passport|neet|rti\b|ayushman", re.I)
    for name in ("planner.py", "follow_ups.py"):
        text = (BACKEND_DIR / "civicsignal" / name).read_text(encoding="utf-8")
        assert not banned.search(text), name


async def test_no_follow_up_repeats_an_existing_search(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(BLOG_A, BLOG_B)))
    keys = [s.request.cache_key() for s in inv.searches]
    assert len(keys) == len(set(keys))
