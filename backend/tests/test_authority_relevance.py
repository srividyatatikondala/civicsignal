"""
Deterministic tests for source-type classification, primary-source support,
relevance levels, and the status rules that use them. Synthetic data only.
"""

from datetime import date, datetime, timezone

import pytest

from civicsignal.claims import cluster_claims, extract_claims
from civicsignal.detectors.authority import analyze_authority, classify_source
from civicsignal.detectors.relevance import analyze_relevance, assess
from civicsignal.models import (
    DataMode,
    IntegrityStatus,
    Investigation,
    InvestigationStatus,
    LandscapeMetrics,
    RelevanceLevel,
    ResultType,
    SearchResult,
    SourceType,
    Strength,
)
from civicsignal.normalize import canonical_url, group_sources, host_of, registrable_domain
from civicsignal.orchestrator import derive_integrity_status

AS_OF = date(2026, 10, 1)
PM_KISAN = "PM Kisan next installment date 2026"


def _result(link, title="", snippet="", query=PM_KISAN, rid=None):
    r = SearchResult(
        id=rid or f"res_{abs(hash((link, title, snippet))) % 10**8}",
        search_run_id="srch_1",
        engine="google",
        query=query,
        result_type=ResultType.ORGANIC,
        position=1,
        title=title,
        link=link,
        snippet=snippet,
        retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        data_mode=DataMode.MOCK,
    )
    r.canonical_url, r.domain = canonical_url(link), host_of(link)
    r.registrable_domain = registrable_domain(r.domain)
    return r


# --- source type ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://pmkisan.gov.in/", SourceType.OFFICIAL),
        ("https://uidai.gov.in/en/", SourceType.OFFICIAL),
        ("https://mcc.nic.in/ug", SourceType.OFFICIAL),
        ("https://travel.state.gov/passports", SourceType.OFFICIAL),
        ("https://admission.bhu.ac.in/notice", SourceType.OFFICIAL),
        ("https://www.rbi.org.in/notice", SourceType.OFFICIAL),
        ("https://www.youtube.com/watch?v=abcdefghijk", SourceType.USER_GENERATED),
        ("https://x.com/user/status/1", SourceType.USER_GENERATED),
        ("https://www.moneycontrol.com/news/x", SourceType.ESTABLISHED_NEWS),
        ("https://zeenews.india.com/x", SourceType.ESTABLISHED_NEWS),
        ("https://bengali.timesnownews.com/x", SourceType.ESTABLISHED_NEWS),
        ("https://someone.blogspot.com/post", SourceType.TERTIARY),
        ("https://m.dailyhunt.in/news/x", SourceType.TERTIARY),
        ("https://governmentjobhub.com/pm-kisan", SourceType.TERTIARY),  # domain-name signal
        ("https://www.sarkariresult.com/x", SourceType.TERTIARY),
        ("https://www.example.com/aadhaar", SourceType.UNKNOWN),  # not reliably classifiable
        ("https://www.example.in/news", SourceType.UNKNOWN),  # "example" must not match a portal word
    ],
)
def test_classify_source(url, expected):
    host = host_of(url)
    kind, signals = classify_source(host, registrable_domain(host))
    assert kind == expected and signals


def test_dot_com_is_not_judged_and_gov_is_only_a_type():
    kind, signals = classify_source("www.example.com", "example.com")
    assert kind == SourceType.UNKNOWN and signals == ["no reliable classification signal"]


# --- relevance -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "title, snippet, url, expected",
    [
        ("PM Kisan 23rd installment date 2026", "Next installment expected in November.",
         "https://a.in/pm-kisan", RelevanceLevel.HIGH),
        ("Kisan credit card rules", "Banks offer crop loans to farmers.",
         "https://a.in/kcc", RelevanceLevel.LOW),  # one of two anchors: a two-word topic needs both
        ("LAST | English meaning", "definition of last: after everything else", "https://dict.org/last",
         RelevanceLevel.OFF_TOPIC),
        ("PM Kisan 23rd installment date 2024", "The installment was paid in 2024.",
         "https://a.in/pm-kisan-2024", RelevanceLevel.MEDIUM),  # year mismatch lowers HIGH
        ("Farmers scheme news", "Amount credited", "https://a.in/pm-kisan-yojana",
         RelevanceLevel.MEDIUM),  # anchors found only in the URL path; 2/5 query words -> not HIGH
        ("Bhagaban Kisan | Official Website", "Page Last Updated Date: 21-02-2024, 12:04 PM.",
         "https://www.jnu.ac.in/content/bhagabankisan", RelevanceLevel.LOW),  # "PM" is a clock time here
        ("PM-KISAN Samman Nidhi", "Official portal", "https://pmkisan.gov.in/", RelevanceLevel.MEDIUM),  # 2/5 words
    ],
)
def test_relevance_levels(title, snippet, url, expected):
    level, signals = assess(_result(url, title, snippet), PM_KISAN)
    assert level == expected, signals


def test_year_mismatch_is_explained():
    level, signals = assess(_result("https://a.in/x", "PM Kisan installment date 2024", ""), PM_KISAN)
    assert any("mentions 2024 but not 2026" in s for s in signals)


def test_relevance_finding_is_one_landscape_finding_with_evidence():
    results = [
        _result("https://a.in/pm-kisan", "PM Kisan 23rd installment date 2026", "Expected in November 2026."),
        _result("https://dict.org/last", "LAST | meaning", "definition of last"),
        _result("https://movies.com/trailer", "The Last Sunrise trailer", "Premieres August 26."),
    ]
    out = analyze_relevance(results, PM_KISAN)
    (f,) = out.findings
    assert f.level == "off_topic" and f.strength == Strength.HIGH  # 2 of 3 weak
    assert len(f.result_ids) == 2 and f.total_results == 3
    assert {e.text for e in out.evidence_spans} >= {"LAST | meaning", "The Last Sunrise trailer"}
    assert "describes what the search returned, not the topic" in f.explanation
    assert results[0].relevance == RelevanceLevel.HIGH and results[0].relevance_signals


def test_all_relevant_results_produce_no_relevance_finding():
    results = [_result("https://a.in/pm-kisan", "PM Kisan installment date 2026", "Next PM Kisan installment.")]
    assert analyze_relevance(results, PM_KISAN).findings == []


# --- primary-source support ---------------------------------------------------------------


def _authority(results, query=PM_KISAN):
    sources = group_sources(results)
    analyze_relevance(results, query)
    ex = extract_claims(results, AS_OF, topic=query)
    clusters = cluster_claims(ex.claims)
    return analyze_authority(results, sources, ex.claims, clusters, query), sources


def test_no_topic_relevant_official_source_gives_limited_coverage():
    (findings, support, counts, primary), _ = _authority([
        _result("https://blog.example.com/pm-kisan", "PM Kisan 23rd installment", "The 23rd installment is expected in July 2026."),
        _result("https://finhry.gov.in/last-pay", "Last pay certificate", "Treasury rules amendment."),  # official, off-topic
    ])
    (f,) = findings
    assert f.title == "Limited primary-source coverage" and f.strength == Strength.MEDIUM
    assert primary == 0 and counts["official"] == 1
    assert "1 official page(s) appeared but do not address it" in f.explanation
    assert "expected date" in f.explanation  # names the claim value without official backing
    assert "false" not in f.explanation.lower()


def test_official_relevant_source_counts_and_backs_its_claim_value():
    (findings, support, counts, primary), sources = _authority([
        _result("https://pmkisan.gov.in/news", "PM Kisan 23rd installment", "The 23rd installment is expected in July 2026."),
        _result("https://blog.example.com/pm-kisan", "PM Kisan 23rd installment", "The 23rd installment is expected in July 2026."),
    ])
    assert primary == 1 and findings == []  # 1 of 2 sources: not "few"
    (vs,) = support
    official_id = next(s.id for s in sources if s.source_type == SourceType.OFFICIAL)
    assert vs.official_source_ids == [official_id] and len(vs.source_ids) == 2


def test_few_primary_sources_threshold():
    results = [_result("https://pmkisan.gov.in/a", "PM Kisan installment 2026", "PM Kisan update.")] + [
        _result(f"https://site{i}.example.com/pm-kisan", "PM Kisan installment 2026", "PM Kisan update.")
        for i in range(4)
    ]
    (findings, _, _, primary), _ = _authority(results)
    (f,) = findings
    assert primary == 1 and f.title == "Few primary sources among results" and f.strength == Strength.LOW


# --- status rules ---------------------------------------------------------------------------


def _inv(findings_kinds, relevance_levels):
    from civicsignal.models import RelevanceFinding, FindingScope

    inv = Investigation(id="inv", query="q", created_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
                        as_of_date=AS_OF, status=InvestigationStatus.COMPLETED)
    inv.results = [_result("https://a.in/x")]
    inv.metrics = LandscapeMetrics(relevance_levels=relevance_levels)
    for _ in findings_kinds:
        inv.findings.append(RelevanceFinding(id="f", detector="relevance", title="t", explanation="e",
                                             strength=Strength.LOW, scope=FindingScope.LANDSCAPE, level="low"))
    return inv


def test_majority_off_topic_is_insufficient_evidence():
    assert derive_integrity_status(_inv(["relevance"], {"off_topic": 6, "high": 4})) == IntegrityStatus.INSUFFICIENT_EVIDENCE


def test_only_minor_findings_is_minor_issues_and_none_is_not_assessed():
    assert derive_integrity_status(_inv(["relevance"], {"off_topic": 1, "high": 9})) == IntegrityStatus.MINOR_ISSUES
    assert derive_integrity_status(_inv([], {"high": 10})) == IntegrityStatus.NOT_ASSESSED  # never CLEAR


def test_short_anchor_counts_only_next_to_another_anchor():
    level, signals = assess(_result("https://x.in/a", "UG admissions 2026", "Merit list for UG programmes."),
                            "NEET UG 2026 exam date")
    assert level == RelevanceLevel.OFF_TOPIC and "does not mention: neet, ug" in signals
    level, _ = assess(_result("https://x.in/b", "NEET UG 2026 exam date announced", "NEET UG exam on 3 May."),
                      "NEET UG 2026 exam date")
    assert level == RelevanceLevel.HIGH


def test_single_short_anchor_query_still_matches():
    level, _ = assess(_result("https://x.in/c", "UG admission schedule", "UG admission dates."), "UG admission dates")
    assert level != RelevanceLevel.OFF_TOPIC
