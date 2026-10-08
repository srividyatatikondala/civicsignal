"""
Deterministic tests for typed claims: amounts, subject qualifiers, amount
attributes, extraction, and clustering. All synthetic text.
"""

from datetime import date, datetime, timezone

import pytest

from civicsignal.claims import (
    amount_attribute,
    cluster_claims,
    entity_qualifier,
    extract_amounts,
    extract_claims,
    format_inr,
    query_anchors,
    subject_qualifiers,
)
from civicsignal.models import ClaimType, DataMode, ResultType, SearchResult, TemporalRole, TemporalStatus

AS_OF = date(2026, 10, 1)


def _result(snippet, title="", rid="res_1", source_id="src_a", position=1):
    return SearchResult(
        id=rid,
        search_run_id="srch_1",
        source_id=source_id,
        engine="google",
        query="q",
        result_type=ResultType.ORGANIC,
        position=position,
        title=title,
        link=f"https://example.in/{source_id}",
        snippet=snippet,
        retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        data_mode=DataMode.MOCK,
    )


# --- amounts -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, value, low, high, aggregate",
    [
        ("₹2,000", 2000, 2000, 2000, False),
        ("Rs. 10/-", 10, 10, 10, False),
        ("Rs 50", 50, 50, 50, False),
        ("INR 1000", 1000, 1000, 1000, False),
        ("1,000 rupees", 1000, 1000, 1000, False),
        ("₹1.5 lakh", 150000, 150000, 150000, True),
        ("Rs 5,653 crore", 5.653e10, 5.653e10, 5.653e10, True),
        ("₹3,590 Cr", 3.59e10, 3.59e10, 3.59e10, True),
        ("about Rs 4,000", 4000, 3600, 4400, False),
        ("over ₹2,650", 2650, 2650, None, False),
        ("up to ₹5,00,000", 500000, 0, 500000, False),
        ("nearly ₹100", 100, 90, 100, False),
    ],
)
def test_amount_parsing(text, value, low, high, aggregate):
    (a,) = extract_amounts(text)
    assert a.value == pytest.approx(value) and a.low == pytest.approx(low)
    assert (a.high is None and high is None) or a.high == pytest.approx(high)
    assert a.aggregate is aggregate


@pytest.mark.parametrize("text", ["2000 farmers", "Rs", "in 2026", "9.3 crore farmers"])
def test_numbers_without_currency_are_not_money(text):
    assert extract_amounts(text) == []


def test_format_inr():
    assert format_inr(2000) == "₹2,000"
    assert format_inr(5.653e10) == "₹5,653 crore"
    assert format_inr(150000) == "₹1.5 lakh"


# --- subject qualifiers -----------------------------------------------------------


def _qualifiers(text, value, title=""):
    i = text.index(value)
    return subject_qualifiers(text, (0, len(text)), (i, i + len(value)), title)


@pytest.mark.parametrize(
    "text, value, expected",
    [
        ("The 23rd installment is expected in July 2026", "July 2026", ["installment:23"]),
        ("The second Rythu Bharosa installment of Rs 400", "Rs 400", ["installment:2"]),
        ("In the second phase Rs 400 will be paid", "Rs 400", ["installment:2"]),
        ("Counselling round 3 ends on 5 August 2026", "5 August 2026", ["round:3"]),
        ("free for all children aged 5-17 until 30 September 2026", "30 September 2026",
         ["age:5-17", "population:children"]),
        ("The deadline is June 14, 2026", "June 14, 2026", []),
    ],
)
def test_subject_qualifiers(text, value, expected):
    assert _qualifiers(text, value) == expected


def test_nearest_ordinal_wins_within_clause():
    text = "The 23rd installment came in March; the 24th installment is expected in November 2026"
    assert _qualifiers(text, "November 2026") == ["installment:24"]


def test_title_ordinal_used_only_when_clause_has_none_and_title_is_unambiguous():
    assert _qualifiers("Expected around July 2026", "July 2026", title="PM Kisan 23rd Installment") == [
        "installment:23"
    ]
    assert _qualifiers("Expected around July 2026", "July 2026", title="23rd and 24th installment") == []


def test_coordinated_ordinals_make_the_subject_ambiguous_and_not_comparable():
    text = "The 23rd and 24th installments are expected in November 2026"
    assert _qualifiers(text, "November 2026") == ["installment:ambiguous"]
    (c,) = extract_claims([_result(text)], AS_OF).claims
    assert c.comparable is False


# --- topic anchoring (entity identity) ------------------------------------------------


@pytest.mark.parametrize(
    "query, anchors",
    [
        ("Aadhaar free update last date 2026", {"aadhaar"}),
        ("PM Kisan next installment date 2026", {"pm", "kisan"}),
        ("PAN Aadhaar link last date 2026", {"pan", "aadhaar"}),
        ("West Bengal Ayushman Bharat deadline 2026", {"west", "bengal", "ayushman", "bharat"}),
        ("RTI application fee central government", {"rti"}),
        ("NEET UG 2026 exam date", {"neet", "ug"}),
    ],
)
def test_query_anchors(query, anchors):
    assert query_anchors(query) == anchors


@pytest.mark.parametrize(
    "clause, page, expected",
    [
        # 1. clause mentions the topic -> topic, even next to an authority acronym
        ("UIDAI free Aadhaar update deadline to June 14 2026", "", None),
        ("Free update on the myAadhaar portal till 14 June 2026", "", None),  # compound token
        # 2. clause names a different entity and not the topic -> that entity
        ("EPFO e-KYC deadline is December 31, 2026", "", "entity:epfo"),
        # 3. clause names nothing, page covers another entity -> unresolved
        ("update their email address until December 31, 2026",
         "EPFO Services Resume, Aadhaar Email Updates Turn Free", "entity:unresolved"),
        # 4. clause names nothing, page names nothing else -> topic
        ("The last date is June 14, 2026", "Free Aadhaar update guide", None),
        # shouty title words and units are not entities
        ("The last date is June 14, 2026", "FREE UPDATE LIVE NEWS 4:00 PM", None),
        # two foreign entities in the clause -> unresolved
        ("EPFO and ESIC deadline is December 31, 2026", "", "entity:unresolved"),
    ],
)
def test_entity_qualifier(clause, page, expected):
    assert entity_qualifier(clause, f"{page} | {clause}", {"aadhaar"}) == expected


def _topic_conflicts(topic, *pairs):
    """pairs of (title, snippet), one per source; returns conflicting value lists."""
    from civicsignal.detectors.contradiction import detect_contradictions

    results = [
        _result(snippet, title=title, rid=f"res_{i}", source_id=f"src_{i}")
        for i, (title, snippet) in enumerate(pairs)
    ]
    ex = extract_claims(results, AS_OF, topic=topic)
    return [f.conflicting_values for f in detect_contradictions(cluster_claims(ex.claims), ex.claims)]


AADHAAR = "Aadhaar free update last date 2026"


def test_other_programme_on_round_up_page_does_not_conflict_with_topic():
    assert _topic_conflicts(
        AADHAAR,
        ("Free Aadhaar update deadline", "Free Aadhaar update till June 14, 2026."),
        ("EPFO Services Resume, Aadhaar Email Updates Turn Free",
         "Members can update their email address until December 31, 2026."),
    ) == []


def test_clause_naming_other_programme_does_not_conflict_with_topic():
    assert _topic_conflicts(
        AADHAAR,
        ("", "Free Aadhaar update till June 14, 2026."),
        ("", "The EPFO e-KYC deadline is December 31, 2026."),
    ) == []


def test_same_other_programme_can_still_conflict_with_itself():
    assert _topic_conflicts(
        AADHAAR,
        ("", "The EPFO e-KYC deadline is December 31, 2026."),
        ("", "EPFO e-KYC last date: March 31, 2027."),
    ) == [["2026-12-31", "2027-03-31"]]


def test_topic_conflicts_still_detected_with_authority_acronyms_and_unanchored_clauses():
    # zeenews-style truncated snippet: no anchor in the clause, no other entity on the page
    assert _topic_conflicts(
        AADHAAR,
        ("", "UIDAI free Aadhaar update deadline to June 14 2026."),
        ("", "card holders can update their details for free till 14 June 2027."),
    ) == [["2026-06-14", "2027-06-14"]]


def test_without_a_topic_behaviour_falls_back_to_result_query():
    # _result() uses query "q" -> anchor {"q"}: unanchored clauses with no other entity stay topic
    ex = extract_claims([_result("The deadline is June 14, 2026.")], AS_OF)
    assert ex.claims[0].subject == ""


# --- amount attributes ------------------------------------------------------------


@pytest.mark.parametrize(
    "text, value, attribute",
    [
        ("The application fee is Rs. 10/-", "Rs. 10/-", "fee"),
        ("pay the applicable ₹1000 late fee", "₹1000", "late_fee"),
        ("A penalty of ₹1,000 applies", "₹1,000", "late_fee"),
        ("Annual assistance increased to ₹9,000", "₹9,000", "annual_benefit"),
        ("₹6,000 per year in three parts", "₹6,000", "annual_benefit"),
        ("each installment of ₹2,000", "₹2,000", "installment_amount"),
        ("health cover of ₹5,00,000 per family", "₹5,00,000", "coverage"),
        ("₹2,000 Payment on 20 June", "₹2,000", "amount"),  # no attribute cue
    ],
)
def test_amount_attributes(text, value, attribute):
    i = text.index(value)
    assert amount_attribute(text, (0, len(text)), (i, i + len(value))) == attribute


# --- extraction --------------------------------------------------------------------


def test_extraction_produces_typed_claims_with_exact_evidence():
    snippet = "The 23rd installment is expected in July 2026. The application fee is Rs. 10/-."
    ex = extract_claims([_result(snippet)], AS_OF)
    by_type = {c.claim_type: c for c in ex.claims}
    d, a = by_type[ClaimType.DATE], by_type[ClaimType.AMOUNT]
    assert (d.subject, d.attribute, d.value, d.role) == ("installment:23", "expected_date", "2026-07-01/2026-07-31",
                                                         TemporalRole.EXPECTED_EVENT)
    assert d.temporal_status == TemporalStatus.PASSED and d.comparable
    assert (a.subject, a.attribute, a.value, a.comparable) == ("", "fee", "₹10", True)
    spans = {e.id: e for e in ex.evidence_spans}
    for c in ex.claims:
        assert c.source_id == "src_a" and c.result_id == "res_1"
        for eid in c.evidence_span_ids:
            assert snippet[spans[eid].start : spans[eid].end] == spans[eid].text == c.claim_text


@pytest.mark.parametrize(
    "snippet, attribute",
    [
        ("The previous deadline was June 14, 2025.", "historical_date"),
        ("Last Updated: April 07, 2026", "publication_date"),
        ("Released on March 3, 2026.", "event_date"),
        ("Premieres September 9, 2026.", "unspecified_date"),
        ("Rs 5,653 crore will be transferred", "aggregate_amount"),
        ("₹2,000 Payment on 20 June", "amount"),
    ],
)
def test_non_comparable_claims_are_kept_but_not_comparable(snippet, attribute):
    (c,) = [c for c in extract_claims([_result(snippet)], AS_OF).claims if c.attribute == attribute]
    assert c.comparable is False


@pytest.mark.parametrize(
    "text",
    [
        "Who is exempted from fees of Rs 10?",
        "Is the last date June 14, 2026?",
        "What did the cabinet decide on 11 September 2024?",
    ],
)
def test_questions_are_not_claims(text):
    assert extract_claims([_result("", title=text)], AS_OF).claims == []
    assert extract_claims([_result(text)], AS_OF).claims == []


def test_statement_after_a_question_is_still_a_claim():
    (c,) = extract_claims([_result("When is the deadline? The deadline is June 14, 2026.")], AS_OF).claims
    assert c.attribute == "deadline"


def test_ordinals_do_not_qualify_scheme_level_amounts():
    r = _result("Annual assistance increased to ₹9,000.", title="PM Kisan 23rd Installment Date 2026")
    (c,) = extract_claims([r], AS_OF).claims
    assert (c.attribute, c.subject) == ("annual_benefit", "")
    r = _result("Each installment of ₹2,000 is paid directly.", title="PM Kisan 23rd Installment Date 2026")
    (c,) = extract_claims([r], AS_OF).claims
    assert (c.attribute, c.subject) == ("installment_amount", "installment:23")


def test_title_and_snippet_repeat_is_one_claim():
    title = "Last Date June 14, 2026"
    ex = extract_claims([_result(f"{title} | more", title=title)], AS_OF)
    (c,) = ex.claims
    assert len(c.evidence_span_ids) == 2


# --- clustering ----------------------------------------------------------------------


def _claims(*snippets):
    results = [_result(s, rid=f"res_{i}", source_id=f"src_{i}") for i, s in enumerate(snippets)]
    return extract_claims(results, AS_OF).claims


def test_overlapping_precisions_agree():
    clusters = cluster_claims(_claims("The deadline is July 2026.", "The deadline is 20 July 2026."))
    (c,) = clusters
    assert not c.has_conflict and len(c.value_groups) == 1


def test_disjoint_values_conflict_and_groups_are_value_ordered_not_count_ordered():
    claims = _claims(
        "The deadline is June 14, 2027.",
        "The deadline is June 14, 2026.",
        "Last date: June 14, 2026.",
        "Free update till 14 June 2026.",
    )
    (c,) = cluster_claims(claims)
    assert c.has_conflict
    assert [g.value for g in c.value_groups] == ["2026-06-14", "2027-06-14"]  # by value, not majority
    assert [len(g.source_ids) for g in c.value_groups] == [3, 1]


def test_approximate_amounts_use_intervals():
    agree = cluster_claims(_claims("The fee is about Rs 4,000.", "The fee is Rs 4,200."))
    assert not agree[0].has_conflict
    disagree = cluster_claims(_claims("The fee is about Rs 4,000.", "The fee is Rs 5,653."))
    assert disagree[0].has_conflict


def test_different_subjects_and_attributes_are_separate_clusters():
    clusters = cluster_claims(
        _claims(
            "The 23rd installment is expected in July 2026.",
            "The 24th installment is expected in November 2026.",
            "The deadline is June 14, 2026.",
            "The installment is expected in June 2026.",
        )
    )
    keys = sorted((c.subject, c.attribute) for c in clusters)
    assert keys == [("", "deadline"), ("", "expected_date"), ("installment:23", "expected_date"),
                    ("installment:24", "expected_date")]
    assert not any(c.has_conflict for c in clusters)
