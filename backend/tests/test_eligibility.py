"""
Phase 5.5: relevance -> claim eligibility.

Off-topic / weakly relevant results keep their claims as evidence but cannot
create or join a conflict. Relevant results participate normally. Runs the
full pipeline with scripted SerpApi responses (no network).
"""

from datetime import date

import httpx
import pytest

from civicsignal.claims import cluster_claims, extract_claims
from civicsignal.db import Repository
from civicsignal.detectors.contradiction import detect_contradictions
from civicsignal.models import InvestigateRequest
from civicsignal.orchestrator import Investigator
from civicsignal.serp import SerpApiClient

pytestmark = pytest.mark.anyio

AADHAAR = "Aadhaar free update last date 2026"
WEST_BENGAL = "West Bengal Ayushman Bharat deadline 2026"


def _serp(*organic):
    body = {
        "search_metadata": {"id": "x", "status": "Success"},
        "organic_results": [
            {"position": i + 1, "title": t, "link": link, "snippet": s} for i, (t, link, s) in enumerate(organic)
        ],
    }
    return httpx.MockTransport(lambda request: httpx.Response(200, json=body))


async def _investigate(tmp_settings, query, transport, monkeypatch=None, break_relevance=False):
    settings = tmp_settings.model_copy(update={"planner_enabled": False}) if hasattr(tmp_settings, "planner_enabled") else tmp_settings
    repo = Repository(settings.database_path)
    repo.init_schema()

    async def no_sleep(_):
        return None

    inv = Investigator(settings, SerpApiClient(settings, transport=transport, sleep=no_sleep), repo,
                       today=lambda: date(2026, 10, 1))
    if break_relevance:
        from civicsignal.detectors import relevance

        def boom(*a, **k):
            raise RuntimeError("relevance crashed")

        monkeypatch.setattr(relevance, "analyze_relevance", boom)
    return await inv.investigate(InvestigateRequest(query=query))


RELEVANT_2026 = ("Free Aadhaar update deadline", "https://news.example.in/aadhaar-free-update",
                 "Free Aadhaar document update is available till 14 June 2026.")
RELEVANT_2027 = ("Aadhaar free update extended", "https://daily.example.com/aadhaar-extended",
                 "Aadhaar holders can update details for free till 14 June 2027.")
OFF_TOPIC_2027 = ("The Last Sunrise booking guide", "https://movies.example.org/last-sunrise",
                  "Book your tickets online: the last date for advance booking is till 14 June 2027.")


def _conflicts(inv):
    return [f for f in inv.findings if f.kind == "contradiction"]


async def test_off_topic_result_cannot_create_a_cross_source_conflict(tmp_settings):
    inv = await _investigate(tmp_settings, AADHAAR, _serp(RELEVANT_2026, OFF_TOPIC_2027))
    assert _conflicts(inv) == []
    off = next(c for c in inv.claims if c.value == "2027-06-14")
    assert off.eligibility.value == "result_off_topic" and off.comparable is False
    assert "does not address the question" in off.eligibility_reason
    assert off.evidence_span_ids  # preserved as evidence, not deleted
    results = {r.id: r for r in inv.results}
    assert results[off.result_id].relevance.value == "off_topic"


def test_counterfactual_without_relevance_gate_the_off_topic_claim_would_conflict(tmp_settings):
    """Proves the test above is meaningful: claim identity alone would have compared them."""
    import asyncio

    inv = asyncio.run(_investigate(tmp_settings, AADHAAR, _serp(RELEVANT_2026, OFF_TOPIC_2027)))
    ex = extract_claims(inv.results, inv.as_of_date, topic=AADHAAR)  # no relevance gate applied
    conflicts = detect_contradictions(cluster_claims(ex.claims), ex.claims)
    assert [f.conflicting_values for f in conflicts] == [["2026-06-14", "2027-06-14"]]


async def test_relevant_results_still_conflict_normally(tmp_settings):
    inv = await _investigate(tmp_settings, AADHAAR, _serp(RELEVANT_2026, RELEVANT_2027))
    (f,) = _conflicts(inv)
    assert f.conflicting_values == ["2026-06-14", "2027-06-14"]
    assert all(c.eligibility.value == "eligible" for c in inv.claims if c.attribute == "deadline")


async def test_off_topic_result_does_not_join_an_existing_conflict(tmp_settings):
    inv = await _investigate(
        tmp_settings, AADHAAR,
        _serp(RELEVANT_2026, RELEVANT_2027,
              ("Concert tickets", "https://music.example.org/tickets", "Ticket sales close on 31 December 2026.")),
    )
    (f,) = _conflicts(inv)
    assert f.conflicting_values == ["2026-06-14", "2027-06-14"]  # the off-topic 2026-12-31 is not a third value


async def test_weakly_relevant_result_is_excluded(tmp_settings):
    weak = ("Bengal tourism update", "https://travel.example.in/bengal",
            "Bengal hotel bookings: the deadline is 30 June 2026.")  # 1 of 4 anchors -> LOW
    strong = ("West Bengal Ayushman Bharat card", "https://news.example.in/wb-ayushman",
              "West Bengal Ayushman Bharat registration deadline is 31 July 2026.")
    inv = await _investigate(tmp_settings, WEST_BENGAL, _serp(weak, strong))
    assert _conflicts(inv) == []
    weak_claim = next(c for c in inv.claims if c.value == "2026-06-30")
    assert weak_claim.eligibility.value == "result_weakly_relevant" and not weak_claim.comparable


async def test_unassessed_relevance_is_not_silently_comparable(tmp_settings, monkeypatch):
    inv = await _investigate(tmp_settings, AADHAAR, _serp(RELEVANT_2026, RELEVANT_2027), monkeypatch,
                             break_relevance=True)
    runs = {d.detector: d.status.value for d in inv.detector_runs}
    assert runs["relevance"] == "unavailable" and runs["contradiction"] == "ok"
    assert _conflicts(inv) == []
    assert {c.eligibility.value for c in inv.claims if c.attribute == "deadline"} == {"relevance_unknown"}
    assert len(inv.claims) == 2  # preserved


async def test_other_exclusion_reasons_are_recorded(tmp_settings):
    inv = await _investigate(
        tmp_settings, AADHAAR,
        _serp(("Aadhaar history", "https://a.example.in/history", "The previous Aadhaar deadline was June 14, 2025."),
              ("Aadhaar update", "https://b.example.in/update", "Aadhaar: the 23rd and 24th installments are expected in May 2026.")),
    )
    states = {c.value: c.eligibility.value for c in inv.claims}
    assert states["2025-06-14"] == "not_comparable_attribute"
    assert states["2026-05-01/2026-05-31"] == "subject_unresolved"


# --- procurement notices and staleness gating ----------------------------------------------


@pytest.mark.parametrize(
    "title, link, snippet",
    [
        ("Amendments to RFP for Toll Free Number", "https://a.gov.in/rfp-1", "Aadhaar helpline RFP: last date 25 July 2026."),
        ("Premises on lease", "https://bank.example.in/tenders/zonal/26-08", "PAN copy required. Last date 22 August 2026."),
        ("Corrigendum - Aadhaar kits", "https://b.gov.in/c", "Bid submission deadline extended till 5 September 2026."),
        ("Aadhaar enrolment kits", "https://c.gov.in/e-procurement/notice", "Last date 1 August 2026."),
    ],
)
def test_procurement_notice_detection(title, link, snippet):
    from civicsignal.claims.extract import is_procurement_notice
    from civicsignal.models import DataMode, ResultType, SearchResult
    from datetime import datetime, timezone

    r = SearchResult(id="r", search_run_id="s", engine="google", query="q", result_type=ResultType.ORGANIC,
                     title=title, link=link, snippet=snippet, retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                     data_mode=DataMode.MOCK)
    assert is_procurement_notice(r)


@pytest.mark.parametrize(
    "title, snippet",
    [
        ("Aadhaar free update deadline", "Free Aadhaar update till 14 June 2026."),
        ("Contender for the title", "Attendees can register until 14 June 2026."),  # 'tender' inside a word
    ],
)
def test_ordinary_pages_are_not_procurement_notices(title, snippet):
    from civicsignal.claims.extract import is_procurement_notice
    from civicsignal.models import DataMode, ResultType, SearchResult
    from datetime import datetime, timezone

    r = SearchResult(id="r", search_run_id="s", engine="google", query="q", result_type=ResultType.ORGANIC,
                     title=title, link="https://news.example.in/aadhaar", snippet=snippet,
                     retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc), data_mode=DataMode.MOCK)
    assert not is_procurement_notice(r)


TENDER_STALE = ("Aadhaar kit tender notice", "https://procure.example.in/aadhaar-kits",
                "Aadhaar enrolment kit tender: bid submission last date 30 June 2026.")
TENDER_OTHER = ("Aadhaar kit tender corrigendum", "https://procure.example.in/aadhaar-kits-2",
                "Aadhaar enrolment kit tender: last date extended till 30 July 2026.")


async def test_procurement_dates_are_kept_but_neither_stale_nor_conflicting(tmp_settings):
    inv = await _investigate(tmp_settings, AADHAAR, _serp(TENDER_STALE, TENDER_OTHER, RELEVANT_2026))
    assert not [f for f in inv.findings if f.kind == "contradiction"]
    stale_values = {f.date_end for f in inv.findings if f.kind == "staleness"}
    assert stale_values == {"2026-06-14"}  # only the relevant, non-procurement deadline
    tender_claims = [c for c in inv.claims if c.eligibility.value == "procurement_notice"]
    assert len(tender_claims) == 2 and all(c.evidence_span_ids for c in tender_claims)


async def test_off_topic_result_is_not_flagged_stale_but_relevant_one_is(tmp_settings):
    inv = await _investigate(tmp_settings, AADHAAR, _serp(RELEVANT_2026, OFF_TOPIC_2026))
    results = {r.id: r for r in inv.results}
    stale = [results[f.result_ids[0]].domain for f in inv.findings if f.kind == "staleness"]
    assert stale == ["news.example.in"]


OFF_TOPIC_2026 = ("The Last Sunrise booking guide", "https://movies.example.org/last-sunrise-2",
                  "Advance booking was open till 14 June 2026.")
