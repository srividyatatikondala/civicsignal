"""
Deterministic tests for the contradiction detector (within-source and
cross-source), including the cases that must NOT be treated as conflicts.
"""

from datetime import date, datetime, timezone

import pytest

from civicsignal.claims import cluster_claims, extract_claims
from civicsignal.detectors.contradiction import detect_contradictions
from civicsignal.models import DataMode, FindingScope, ResultType, SearchResult, Strength

AS_OF = date(2026, 10, 1)


def _result(snippet, title="", rid="res_1", source_id="src_a"):
    return SearchResult(
        id=rid,
        search_run_id="srch_1",
        source_id=source_id,
        engine="google",
        query="q",
        result_type=ResultType.ORGANIC,
        position=1,
        title=title,
        link=f"https://example.in/{source_id}",
        snippet=snippet,
        retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        data_mode=DataMode.MOCK,
    )


def _detect(results):
    ex = extract_claims(results, AS_OF)
    clusters = cluster_claims(ex.claims)
    return detect_contradictions(clusters, ex.claims), ex


def _sources(*snippets):
    return [_result(s, rid=f"res_{i}", source_id=f"src_{i}") for i, s in enumerate(snippets)]


# --- within-source ---------------------------------------------------------------


def test_within_source_title_vs_snippet():
    r = _result("Apply soon. The deadline is 31 July 2026.", title="Last date 30 June 2026")
    findings, _ = _detect([r])
    (f,) = findings
    assert f.scope == FindingScope.WITHIN_SOURCE and f.title == "Internal contradiction"
    assert f.conflicting_values == ["2026-06-30", "2026-07-31"]
    assert f.source_ids == ["src_a"] and f.strength == Strength.HIGH


def test_within_source_across_two_results_of_same_page():
    # e.g. the answer box and the organic listing of one canonical page
    results = [
        _result("The deadline is 30 June 2026.", rid="res_box", source_id="src_a"),
        _result("The deadline is 31 July 2026.", rid="res_org", source_id="src_a"),
    ]
    findings, _ = _detect(results)
    (f,) = findings
    assert f.scope == FindingScope.WITHIN_SOURCE
    assert set(f.result_ids) == {"res_box", "res_org"}


def test_within_source_evidence_is_preserved():
    r = _result("The deadline is 31 July 2026.", title="Last date 30 June 2026")
    findings, ex = _detect([r])
    spans = {e.id: e for e in ex.evidence_spans}
    texts = sorted(spans[eid].text for eid in findings[0].evidence_span_ids)
    assert texts == ["Last date 30 June 2026", "The deadline is 31 July 2026."]


# --- cross-source ----------------------------------------------------------------


def test_cross_source_conflict_lists_every_value_and_source_without_majority():
    findings, ex = _detect(
        _sources(
            "The deadline is June 14, 2026.",
            "Free update till 14 June 2026.",
            "Last date: June 14, 2026.",
            "Free update till 14 June 2027.",
        )
    )
    (f,) = findings
    assert f.scope == FindingScope.CROSS_SOURCE
    assert f.conflicting_values == ["2026-06-14", "2027-06-14"]
    assert [g.source_ids for g in f.value_groups] == [["src_0", "src_1", "src_2"], ["src_3"]]
    assert len(f.source_ids) == 4 and len(f.claim_ids) == 4
    assert len(f.evidence_span_ids) == 4  # every source's evidence is kept
    text = f.explanation.lower()
    assert "does not make it correct" in text
    for banned in ("true", "false", "correct value is", "majority"):
        assert banned not in text.replace("does not make it correct", "")


def test_cross_source_amount_conflict():
    findings, _ = _detect(_sources("The application fee is Rs 10.", "The application fee is Rs 50."))
    (f,) = findings
    assert f.attribute == "fee" and f.conflicting_values == ["₹10", "₹50"]


def test_installment_specific_conflict():
    findings, _ = _detect(
        _sources(
            "The 23rd installment will be released on 20 June 2026.",
            "The 23rd installment is expected around July 2026.",
        )
    )
    (f,) = findings
    assert f.subject == "installment:23" and f.attribute == "expected_date"
    assert f.conflicting_values == ["2026-06-20", "2026-07-01/2026-07-31"]


def test_approximate_values_lower_strength():
    findings, _ = _detect(_sources("The fee is about Rs 4,000.", "The fee is Rs 5,653."))
    (f,) = findings
    assert f.strength == Strength.LOW


def test_both_scopes_when_one_source_is_internally_inconsistent():
    results = [
        _result("The deadline is 31 July 2026.", title="Last date 30 June 2026", rid="r0", source_id="src_a"),
        _result("The deadline is 30 June 2026.", rid="r1", source_id="src_b"),
    ]
    findings, _ = _detect(results)
    assert sorted(f.scope.value for f in findings) == ["cross_source", "within_source"]


# --- NOT conflicts -----------------------------------------------------------------


@pytest.mark.parametrize(
    "snippets",
    [
        # historical vs current state (requirement: not automatically a conflict)
        ["The deadline was June 14, 2025. The current deadline is June 14, 2026."],
        ["The previous deadline was June 14, 2025.", "The deadline is June 14, 2026."],
        ["The deadline was extended from June 14, 2025 to June 14, 2026."],
        # different subjects
        ["The 23rd installment is expected in July 2026.", "The 24th installment is expected in November 2026."],
        ["Free for children aged 5-17 until 30 September 2026.", "Free update till 14 June 2026."],
        # different attributes
        ["The deadline is June 14, 2026.", "The result is expected in August 2026."],
        ["The application fee is Rs 10.", "A late fee of Rs 1000 applies."],
        # same value at different precision
        ["The deadline is July 2026.", "The deadline is 20 July 2026."],
        # same value, repeated (agreement, not conflict)
        ["The fee is ₹10.", "The standard application fee is Rs. 10/-."],
        # aggregates are never compared
        ["Rs 4,000 crore will be credited.", "Rs 5,653 crore will be transferred."],
        # publication / event dates are not claims about the deadline
        ["Last Updated: April 07, 2026", "The deadline is June 14, 2026."],
        # birth-date eligibility rule vs deadline
        ["Candidates born on or before 31 December 2008 are eligible.", "Apply by 15 March 2026."],
    ],
)
def test_not_a_conflict(snippets):
    findings, _ = _detect(_sources(*snippets))
    assert findings == []


def test_single_source_single_value_is_not_a_conflict():
    findings, _ = _detect([_result("The deadline is June 14, 2026.")])
    assert findings == []
