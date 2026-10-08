"""
Phase 6.5: base-search vs follow-up evidence roles, status, and follow-up outcomes.
Synthetic, scripted SerpApi (no network, no credits).
"""

import httpx
import pytest

from civicsignal.report import build_report

from .test_planner import Router, _body, _run, org

pytestmark = pytest.mark.anyio

TOPIC = "Sample Yojana last date 2026"  # anchor: "sample"

OFF_1 = org("LAST | English meaning", "https://dict.example.com/last", "Definition of last: after everything else.")
OFF_2 = org("The Last Sunrise trailer", "https://movies.example.com/sunrise", "The film premieres on streaming.")
OFF_3 = org("Last seen apps", "https://apps.example.com/lastseen", "Hide your last seen status in chat apps.")

BLOG_DEADLINE = org("Sample Yojana last date", "https://blog.example.com/sample-yojana",
                    "The Sample Yojana last date to apply is 31 December 2026.")
OFFICIAL_SAME_VALUE = org("Sample Yojana - official notice", "https://sampleyojana.gov.in/notice",
                          "Sample Yojana applications: last date 31 December 2026.")
OFFICIAL_NO_VALUE = org("Sample Yojana - official portal", "https://sampleyojana.gov.in/",
                        "Sample Yojana guidelines, eligibility and beneficiary list.")
OFFICIAL_NO_VALUE_2 = org("Sample Yojana FAQs", "https://sampleyojana.gov.in/faq",
                          "Frequently asked questions about Sample Yojana.")
OFFICIAL_NO_VALUE_3 = org("Sample Yojana circulars", "https://dept.state.gov.in/sample-yojana",
                          "Circulars issued for Sample Yojana.")
OFFICIAL_OFF_TOPIC = org("State Labour Portal", "https://labour.state.gov.in/", "Registration of establishments.")
OFFICIAL_OFF_TOPIC_2 = org("Procurement portal", "https://gem.example.gov.in/", "Government e-marketplace home page.")
OFF_TOPIC_CONFLICT = org("Concert ticket booking", "https://music.example.org/tickets",
                         "Booking closes: the last date is 15 March 2027.")


def _role_counts(levels):
    return {k: v for k, v in (levels or {}).items() if v}


# --- A + F: base relevance measured alone; recovered evidence prevents INSUFFICIENT_EVIDENCE ---------


async def test_base_relevance_is_measured_only_on_base_results_and_recovery_is_not_insufficient(tmp_settings):
    router = Router(
        base=_body(OFF_1, OFF_2, OFF_3),
        primary=_body(OFFICIAL_NO_VALUE, OFFICIAL_NO_VALUE_2, OFFICIAL_NO_VALUE_3, OFFICIAL_OFF_TOPIC,
                      OFFICIAL_OFF_TOPIC_2),
    )
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    m = inv.metrics
    assert m.base_results == 3 and m.follow_up_results == 5
    assert _role_counts(m.base_relevance_levels) == {"off_topic": 3}
    assert sum(m.follow_up_relevance_levels.values()) == 5
    assert m.follow_up_relevance_levels.get("off_topic", 0) == 2
    # the relevance finding describes the ORIGINAL search only
    (rel,) = [f for f in inv.findings if f.kind == "relevance"]
    base_ids = {r.id for r in inv.results if r.search_run_id == inv.searches[0].id}
    assert set(rel.result_ids) <= base_ids and rel.total_results == 3
    assert "original search" in rel.explanation
    # 3 relevant official pages recovered -> not INSUFFICIENT_EVIDENCE
    assert inv.integrity_status.value == "MINOR_ISSUES"
    r = build_report(inv)
    assert r.status.explanation.startswith(
        "The original search did not address the question; targeted official-source searches recovered "
        "3 relevant official pages that address the topic."
    )


async def test_nothing_recovered_stays_insufficient(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(OFF_1, OFF_2, OFF_3), primary=_body(OFFICIAL_OFF_TOPIC)),
                        query=TOPIC)
    assert inv.integrity_status.value == "INSUFFICIENT_EVIDENCE"


async def test_base_only_behaviour_unchanged(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(OFF_1, OFF_2, OFF_3)), query=TOPIC, planner_enabled=False)
    assert inv.integrity_status.value == "INSUFFICIENT_EVIDENCE"
    assert _role_counts(inv.metrics.base_relevance_levels) == {"off_topic": 3}
    assert inv.metrics.follow_up_results == 0 and inv.metrics.follow_up_new_sources == 0


# --- official pages found  vs  addressing the topic  vs  supporting a claim value ---------------


async def test_three_official_concepts_stay_separate(tmp_settings):
    router = Router(base=_body(OFF_1, OFF_2, OFF_3),
                    primary=_body(OFFICIAL_NO_VALUE, OFFICIAL_NO_VALUE_2, OFFICIAL_OFF_TOPIC))
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    assert inv.metrics.official_sources == 3  # official pages found
    assert inv.metrics.primary_sources == 2  # official pages that address the topic
    (lookup,) = inv.follow_ups
    assert lookup.official_topic_pages_found is True
    assert lookup.primary_source_support_found is False  # no claim value stated -> no claim support
    assert lookup.outcome_code == "official_found_not_addressing_claims"


# --- B + D: follow-up evidence joins claims and can support a value -----------------------------


async def test_follow_up_official_page_supports_a_specific_value(tmp_settings):
    router = Router(base=_body(BLOG_DEADLINE, OFF_1), primary=_body(OFFICIAL_SAME_VALUE, OFFICIAL_NO_VALUE))
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    follow_run = inv.searches[1].id
    follow_claims = [c for c in inv.claims if any(r.id == c.result_id and r.search_run_id == follow_run
                                                  for r in inv.results)]
    assert follow_claims and any(c.comparable for c in follow_claims)  # B: follow-up claims participate
    (support,) = [v for v in inv.official_support if v.value == "2026-12-31"]
    assert support.official_source_ids  # D
    (lookup,) = inv.follow_ups
    assert lookup.outcome_code == "support_found" and lookup.primary_source_support_found is True
    assert "deadline 2026-12-31" in lookup.values_with_official_support
    for banned in ("true", "false", "correct", "incorrect", "verified"):
        assert banned not in lookup.summary.lower()


# --- E: official page not addressing the claim is not support ------------------------------------


async def test_official_page_without_the_value_is_not_support(tmp_settings):
    router = Router(base=_body(BLOG_DEADLINE, OFF_1), primary=_body(OFFICIAL_NO_VALUE))
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    (support,) = [v for v in inv.official_support if v.value == "2026-12-31"]
    assert support.official_source_ids == []
    (lookup,) = inv.follow_ups
    assert lookup.outcome_code == "official_found_not_addressing_claims"
    assert "deadline 2026-12-31" in lookup.values_not_addressed_by_official_sources
    r = build_report(inv)
    (entry,) = [e for e in r.evidence_map if e.attribute == "deadline"]
    assert entry.values[0].stated_by_official_source is False


# --- C: follow-up off-topic results cannot create conflicts --------------------------------------


async def test_follow_up_off_topic_result_cannot_create_conflict(tmp_settings):
    router = Router(base=_body(BLOG_DEADLINE, OFF_1), primary=_body(OFF_TOPIC_CONFLICT))
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    assert not [f for f in inv.findings if f.kind == "contradiction"]
    off = next(c for c in inv.claims if c.value == "2027-03-15")
    assert off.eligibility.value == "result_off_topic"


# --- new sources: distinct pages first seen in a follow-up ---------------------------------------


async def test_follow_up_new_sources_counts_pages_not_appearances(tmp_settings):
    same_page_again = org("Sample Yojana last date (repeat)", "https://www.blog.example.com/sample-yojana/?utm_source=x",
                          "The Sample Yojana last date to apply is 31 December 2026.")
    router = Router(base=_body(BLOG_DEADLINE, OFF_1), primary=_body(same_page_again, OFFICIAL_NO_VALUE))
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    assert inv.metrics.follow_up_results == 2
    assert inv.metrics.follow_up_new_sources == 1  # the repeated blog page is not new


# --- outcomes for no-results and failed follow-ups --------------------------------------------------


async def test_empty_follow_up_outcome_is_no_results(tmp_settings):
    inv, _ = await _run(tmp_settings, Router(base=_body(OFF_1, BLOG_DEADLINE), primary=_body()), query=TOPIC)
    (lookup,) = [f for f in inv.follow_ups if f.reason.value == "primary_source_lookup"]
    assert lookup.status.value == "empty" and lookup.outcome_code == "no_results"
    assert lookup.primary_source_support_found is False and lookup.official_topic_pages_found is False


async def test_failed_follow_up_outcome_is_recorded_and_investigation_completes(tmp_settings):
    def handler(request):
        if "site:" in request.url.params.get("q", ""):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json=_body(BLOG_DEADLINE, OFF_1))

    inv, _ = await _run(tmp_settings, handler, query=TOPIC, serpapi_max_retries=0)
    assert inv.status.value == "partial"
    (lookup,) = [f for f in inv.follow_ups if f.reason.value == "primary_source_lookup"]
    assert lookup.outcome_code == "failed" and lookup.primary_source_support_found is None
    assert lookup.results_added == 0


async def test_news_and_recency_outcome_codes(tmp_settings):
    stale_expected = org("Sample Yojana installment", "https://blog.example.com/sy-inst",
                         "The Sample Yojana installment is expected in July 2026.")
    news_item = org("Sample Yojana installment released", "https://news.example.in/sy",
                    "Sample Yojana installment released to beneficiaries.")
    router = Router(base=_body(stale_expected, OFFICIAL_NO_VALUE),
                    news=_body(news_item, news=True))
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    (news,) = [f for f in inv.follow_ups if f.reason.value == "news_check"]
    assert news.outcome_code == "additional_relevant_evidence" and news.relevant_results == 1

    stale_deadline = org("Sample Yojana last date", "https://blog.example.com/sy-deadline",
                         "The Sample Yojana last date is 30 June 2026.")
    router = Router(base=_body(stale_deadline, OFFICIAL_NO_VALUE), recency=_body(OFF_1))
    inv, _ = await _run(tmp_settings, router, query=TOPIC, cache_enabled=False)  # same base query as above
    (recency,) = [f for f in inv.follow_ups if f.reason.value == "recency_contrast"]
    assert recency.outcome_code == "no_newer_relevant_evidence"


# --- report / UI labels -----------------------------------------------------------------------------


async def test_report_separates_original_and_targeted_searches(tmp_settings):
    router = Router(base=_body(OFF_1, OFF_2, OFF_3),
                    primary=_body(OFFICIAL_NO_VALUE, OFFICIAL_NO_VALUE_2, OFFICIAL_NO_VALUE_3, OFFICIAL_OFF_TOPIC))
    inv, _ = await _run(tmp_settings, router, query=TOPIC)
    L = build_report(inv).landscape
    assert L.includes_follow_ups is True
    assert L.base_results == 3 and _role_counts(L.base_relevance) == {"off_topic": 3}
    assert L.follow_up_results == 4 and L.follow_up_new_sources == 4 and L.follow_up_relevant_results == 3
    assert L.official_pages_found == 4 and L.official_topic_sources == 3


def test_repeated_content_category_label():
    from civicsignal.report import CATEGORY

    assert CATEGORY["duplication"][1] == "Potentially repeated or syndicated content"
