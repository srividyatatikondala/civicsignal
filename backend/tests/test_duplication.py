"""
Deterministic tests for the duplication / independence detector. Synthetic text only.
"""

from datetime import date, datetime, timezone

import pytest

from civicsignal.claims import cluster_claims, extract_claims
from civicsignal.detectors.duplication import compare_texts, detect_duplication
from civicsignal.models import DataMode, RelationKind, ResultType, SearchResult, Strength
from civicsignal.normalize import canonical_url, group_sources, host_of, registrable_domain

AS_OF = date(2026, 10, 1)
TOPIC = "Aadhaar free update last date 2026"
ANCHORS = {"aadhaar"}

ARTICLE = (
    "The Unique Identification Authority has extended the facility for residents to upload proof of "
    "identity and address documents without charge through the myAadhaar portal until June 14, 2026, "
    "after which enrolment centres will collect the standard service charge from applicants."
)
REWORDED = (
    "The Unique Identification Authority has extended the facility allowing residents to upload proof of "
    "identity and address documents without any charge on the myAadhaar portal until June 14, 2026, "
    "after which enrolment centres will collect the usual service charge from applicants."
)
INDEPENDENT_SAME_CLAIM = (
    "Aadhaar holders who want to refresh ten-year-old records can do so at no cost online until June 14, "
    "2026; a reporter visiting Bengaluru kiosks found long queues as people rushed to beat the cutoff."
)

def _result(snippet, link, title="", rid=None, position=1):
    return SearchResult(
        id=rid or f"res_{abs(hash((link, snippet))) % 10**8}",
        search_run_id="srch_1",
        engine="google",
        query=TOPIC,
        result_type=ResultType.ORGANIC,
        position=position,
        title=title,
        link=link,
        snippet=snippet,
        retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        data_mode=DataMode.MOCK,
    )

def _run(*results):
    """Full independence analysis over results (sources grouped by canonical URL)."""
    results = list(results)
    for r in results:

        r.canonical_url = canonical_url(r.link)
        r.domain = host_of(r.link)
        r.registrable_domain = registrable_domain(r.domain)
    sources = group_sources(results)
    ex = extract_claims(results, AS_OF, topic=TOPIC)
    clusters = cluster_claims(ex.claims)
    return detect_duplication(results, sources, ex.claims, clusters, TOPIC), sources, ex

def _rel(a, b, title_a="", title_b=""):
    return compare_texts(a, b, title_a, title_b, ANCHORS)

# --- pairwise text comparison ------------------------------------------------------------

def test_exact_duplicate_text_is_near_identical():
    rel = _rel(ARTICLE, ARTICLE)
    assert rel.kind == RelationKind.NEAR_IDENTICAL and rel.text_containment == 1.0
    assert rel.shared_phrases and rel.longest_shared_run >= 30

def test_near_identical_rewording_is_detected():
    rel = _rel(ARTICLE, REWORDED)
    assert rel.kind in (RelationKind.NEAR_IDENTICAL, RelationKind.SUBSTANTIAL_OVERLAP)
    assert rel.text_containment >= 0.5

def test_same_claim_in_different_wording_is_not_related():
    assert _rel(ARTICLE, INDEPENDENT_SAME_CLAIM) is None

def test_generic_civic_wording_alone_does_not_relate():
    a = ("Last date to apply online for the scheme is June 14, 2026. Check the official website for "
         "details, eligibility, documents and the application status. Apply online before the last date.")
    b = ("Last date to apply online for the scheme is March 3, 2026. Check the official website for "
         "details, eligibility, documents and the application status. Apply online before the last date.")
    assert _rel(a, b) is None

def test_short_snippets_remain_uncertain():
    short = "Free Aadhaar update till June 14, 2026, says UIDAI."
    assert _rel(short, short) is None

def test_related_headlines_without_snippet_support():
    rel = _rel(
        ARTICLE, INDEPENDENT_SAME_CLAIM,
        "Centre extends free document upload facility for residents till June",
        "Centre extends free document upload facility for residents - Times Daily",
    )
    assert rel.kind == RelationKind.RELATED_TITLE and rel.merges_independence is False

def test_generic_headlines_do_not_relate():
    assert _rel(
        ARTICLE, INDEPENDENT_SAME_CLAIM, "Aadhaar Update Last Date 2026", "Aadhaar Free Update Last Date 2026 - News"
    ) is None

# --- full detector -------------------------------------------------------------------------

def test_syndicated_copy_on_two_domains_is_one_independent_group_with_explained_finding():
    out, sources, _ = _run(
        _result(ARTICLE, "https://dailynews.example.in/aadhaar-free-update"),
        _result(ARTICLE, "https://citytimes.example.com/news/aadhaar-update-extended"),
    )
    assert len(sources) == 2 and out.independent_groups == 1 and out.duplicate_groups == 1
    pair = [f for f in out.findings if f.relation == "near_identical"]
    (f,) = pair
    assert f.strength == Strength.HIGH and f.title == "Near-identical wording"
    assert f.text_containment == 1.0 and f.shared_phrases
    text = f.explanation.lower()
    assert "may not represent independent confirmation" in text
    assert "does not establish who published first" in text
    for banned in ("is false", "fake", "plagiar", "definitely copied"):
        assert banned not in text
    spans = {e.id: e for e in out.evidence_spans}
    assert {spans[i].text for i in f.evidence_span_ids} == {ARTICLE}

def test_repeated_claim_from_related_sources_is_flagged_not_as_false():
    out, _, _ = _run(
        _result(ARTICLE, "https://dailynews.example.in/aadhaar-free-update"),
        _result(ARTICLE, "https://citytimes.example.com/news/aadhaar-update-extended"),
        _result(INDEPENDENT_SAME_CLAIM, "https://metro.example.org/aadhaar-queues"),
    )
    (repeat,) = [f for f in out.findings if f.relation == "repeated_claim"]
    assert repeat.supporting_sources == 3 and repeat.independent_groups == 2
    assert repeat.title == "Repeated claim may not be independently confirmed"
    assert repeat.claim_ids and repeat.cluster_id
    (support,) = [c for c in out.summary.claim_support if c.value == "2026-06-14"]
    assert len(support.source_ids) == 3 and support.independent_groups == 2

def test_same_claim_with_independent_wording_has_full_independence():
    out, _, _ = _run(
        _result(ARTICLE, "https://dailynews.example.in/aadhaar-free-update"),
        _result(INDEPENDENT_SAME_CLAIM, "https://metro.example.org/aadhaar-queues"),
    )
    assert out.findings == [] and out.independent_groups == 2
    (support,) = [c for c in out.summary.claim_support if c.value == "2026-06-14"]
    assert support.independent_groups == 2

def test_same_article_at_different_urls_is_one_source_not_a_duplicate_pair():
    out, sources, _ = _run(
        _result(ARTICLE, "https://www.dailynews.example.in/aadhaar-free-update?utm_source=google", rid="r1"),
        _result(ARTICLE, "https://dailynews.example.in/aadhaar-free-update/amp", rid="r2"),
    )
    assert len(sources) == 1 and len(sources[0].urls) == 2
    assert out.findings == [] and out.independent_groups == 1

def test_same_domain_different_articles_is_same_publisher_not_duplication():
    out, sources, _ = _run(
        _result(ARTICLE, "https://dailynews.example.in/aadhaar-free-update"),
        _result(INDEPENDENT_SAME_CLAIM, "https://dailynews.example.in/bengaluru-queues"),
    )
    (rel,) = out.summary.relationships
    assert rel.kind == RelationKind.SAME_PUBLISHER and rel.shared_domain == "example.in"
    assert not [f for f in out.findings if f.relation != "repeated_claim"]  # no textual-similarity finding
    assert out.independent_groups == 1 and out.duplicate_groups == 0
    (repeat,) = [f for f in out.findings if f.relation == "repeated_claim"]
    assert repeat.strength == Strength.LOW  # publisher link only, no shared wording

def test_platform_domains_are_not_one_publisher():
    out, _, _ = _run(
        _result(ARTICLE, "https://www.youtube.com/watch?v=AAAAAAAAAA1"),
        _result(INDEPENDENT_SAME_CLAIM, "https://www.youtube.com/watch?v=BBBBBBBBBB2"),
    )
    assert out.summary.relationships == [] and out.independent_groups == 2

def test_different_values_in_otherwise_similar_articles_are_noted():
    other_date = ARTICLE.replace("June 14, 2026", "June 14, 2027")
    out, _, _ = _run(
        _result(ARTICLE, "https://dailynews.example.in/aadhaar-free-update"),
        _result(other_date, "https://citytimes.example.com/news/aadhaar-update-extended"),
    )
    (f,) = [f for f in out.findings if f.relation == "near_identical"]
    assert f.differing_values == ["deadline: 2026-06-14 vs 2027-06-14"]
    assert "stated values differ" in f.explanation
    assert not [x for x in out.findings if x.relation == "repeated_claim"]  # different values: no consensus

def test_counts_stay_separate():
    out, sources, _ = _run(
        _result(ARTICLE, "https://dailynews.example.in/aadhaar-free-update", rid="r1"),
        _result(ARTICLE, "https://www.dailynews.example.in/aadhaar-free-update?utm_medium=x", rid="r2"),
        _result(ARTICLE, "https://citytimes.example.com/news/aadhaar-update-extended", rid="r3"),
        _result(INDEPENDENT_SAME_CLAIM, "https://metro.example.org/aadhaar-queues", rid="r4"),
    )
    # 4 result appearances -> 3 sources -> 3 domains -> 2 independent groups
    assert len(sources) == 3
    assert out.independent_groups == 2 and out.duplicate_groups == 1
