"""
Unit tests for the staleness engine (dates, clause scoping, nearest-cue
roles, detector output). Synthetic sentences; real-fixture behaviour is
covered in test_staleness_fixtures.py.
"""

from datetime import date, datetime, timezone

import pytest

from civicsignal.detectors.staleness import (
    StalenessConfig,
    analyze_text,
    clause_bounds,
    extract_date_claims,
    run_staleness,
    severity,
)
from civicsignal.models import (
    DataMode,
    EvidenceField,
    ResultType,
    SearchResult,
    Strength,
    TemporalRole,
)

AS_OF = date(2026, 10, 1)


# --- dates -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, precision, start, end",
    [
        ("14 June 2026", "day", "2026-06-14", "2026-06-14"),
        ("June 14, 2026", "day", "2026-06-14", "2026-06-14"),
        ("14/06/2026", "day", "2026-06-14", "2026-06-14"),
        ("2026-06-14", "day", "2026-06-14", "2026-06-14"),
        ("July 2026", "month", "2026-07-01", "2026-07-31"),
        ("June-July 2026", "month_range", "2026-06-01", "2026-07-31"),
        ("June to July 2026", "month_range", "2026-06-01", "2026-07-31"),
        ("December–January 2026", "month_range", "2025-12-01", "2026-01-31"),
        ("Sept. 3, 2026", "day", "2026-09-03", "2026-09-03"),
    ],
)
def test_date_formats(text, precision, start, end):
    (c,) = extract_date_claims(text)
    assert (c.precision, c.date_start.isoformat(), c.date_end.isoformat()) == (precision, start, end)


def test_ambiguous_numeric_date_is_flagged():
    (c,) = extract_date_claims("Last date 05/06/2026")
    assert c.ambiguous is True
    (c,) = extract_date_claims("Last date 14/06/2026")
    assert c.ambiguous is False


def test_invalid_dates_are_skipped_not_guessed():
    assert extract_date_claims("2026-13-45 and 31/02/2026") == []


def test_iso_date_is_not_reparsed_as_other_patterns():
    claims = extract_date_claims("Last updated 2026-09-16 UTC.")
    assert [c.matched_text for c in claims] == ["2026-09-16"]


# --- clauses -----------------------------------------------------------------


def test_clause_split_does_not_break_dates_or_abbreviations():
    text = "Fee is Rs. 50 after June 14, 2026. Apply soon"
    protected = [(m.start_idx, m.end_idx) for m in extract_date_claims(text)]
    spans = [text[a:b].strip() for a, b in clause_bounds(text, protected)]
    assert spans == ["Fee is Rs. 50 after June 14, 2026.", "Apply soon"]


def test_clause_evidence_is_clean():
    text = "Deadline was 31 December 2025 for holders, and PANs became inoperative. ₹1000 fee applies."
    protected = [(m.start_idx, m.end_idx) for m in extract_date_claims(text)]
    spans = [text[a:b].strip() for a, b in clause_bounds(text, protected)]
    assert spans == [
        "Deadline was 31 December 2025 for holders,",
        "and PANs became inoperative.",
        "₹1000 fee applies.",
    ]


def test_initials_do_not_end_a_clause():
    text = "The U.S. deadline is June 14, 2026."
    protected = [(m.start_idx, m.end_idx) for m in extract_date_claims(text)]
    assert len(clause_bounds(text, protected)) == 1


@pytest.mark.parametrize("sep", [" | ", " · ", "; ", "... ", ", and ", ", but "])
def test_clause_separators(sep):
    text = f"released on March 3, 2026{sep}the deadline is June 14, 2026"
    roles = {m.date_text: m.role for m in analyze_text(text)}
    assert roles == {"March 3, 2026": TemporalRole.PAST_EVENT, "June 14, 2026": TemporalRole.DEADLINE}


# --- roles -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, role",
    [
        ("Free update till 14 June 2026.", TemporalRole.DEADLINE),
        ("available until 30 September 2026", TemporalRole.DEADLINE),
        ("December 31, 2019 is the last date to link.", TemporalRole.DEADLINE),  # cue after the date
        ("CBDT has set December 31, 2025 as the deadline", TemporalRole.DEADLINE),
        ("expected to be released around July 2026, based on previous payment patterns.",
         TemporalRole.EXPECTED_EVENT),  # bare "previous" is not a historical cue
        ("will be released on 20 June 2026.", TemporalRole.EXPECTED_EVENT),  # not past_event
        ("The 22nd installment was released on 2 March 2026.", TemporalRole.PAST_EVENT),
        ("The previous Aadhaar deadline was June 14, 2025.", TemporalRole.HISTORICAL_REFERENCE),
        ("The deadline was extended from June 14, 2025.", TemporalRole.HISTORICAL_REFERENCE),
        ("Last Updated: April 07, 2026 03:38 PM", TemporalRole.PUBLICATION),
        ("Published: 12 September 2026", TemporalRole.PUBLICATION),
        ("rolling registrations since June 2026", TemporalRole.OPEN_ENDED),
        ("Premieres September 9, 2026 on TV.", TemporalRole.UNKNOWN),
    ],
)
def test_roles(text, role):
    (m,) = analyze_text(text)
    assert m.role == role, (m.cue_text, m.clause)


def test_nearest_cue_in_same_clause_wins():
    text = "The deadline, previously June 14, 2025, has been extended to June 14, 2026."
    roles = {m.date_text: m.role for m in analyze_text(text)}
    assert roles == {
        "June 14, 2025": TemporalRole.HISTORICAL_REFERENCE,
        "June 14, 2026": TemporalRole.DEADLINE,
    }


def test_far_cue_is_ignored():
    text = "Deadline information " + "x" * 120 + " the event on June 14, 2026"
    (m,) = analyze_text(text)
    assert m.role == TemporalRole.UNKNOWN


# --- hardening pass: generic deadline prepositions ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Submit the form by 15 March 2026.",
        "Payment must be made before 15 March 2026.",
        "Apply before 15 March 2026.",
        "Register on or before 15 March 2026.",
        "Documents must reach the office no later than 15 March 2026.",
        "Updates are free until 15 March 2026.",
        "Updates are free till 15 March 2026.",
        "Applications close by 15/03/2026.",
        "Fees are due by 2026-03-15.",
    ],
)
def test_deadline_prepositions(text):
    (m,) = analyze_text(text)
    assert m.role == TemporalRole.DEADLINE, (m.cue_text, m.clause)


@pytest.mark.parametrize(
    "text",
    [
        "The rule was set by CBDT on 15 March 2026.",  # "by" governs the agent, not the date
        "Released by the ministry in March 2026.",  # "by" not directly before the date
        "Prices increased by 15 percent in March 2026.",
    ],
)
def test_by_not_adjacent_to_date_is_not_a_cue(text):
    (m,) = analyze_text(text)
    assert m.role == TemporalRole.UNKNOWN, (m.cue_text, m.clause)


@pytest.mark.parametrize(
    "text",
    [
        "Candidates born on or before 31 December 2008 are eligible.",
        "Applicants born after 1 January 2000 may apply.",
        "Date of birth: 01/01/2000",
    ],
)
def test_birth_date_cutoffs_are_eligibility_not_deadlines(text):
    (m,) = analyze_text(text)
    assert m.role == TemporalRole.ELIGIBILITY_CUTOFF
    assert run_staleness([_result(text)], AS_OF).findings == []


# --- hardening pass: explicit past-tense narration ----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "The deadline was June 14, 2025.",
        "December 31, 2019 was the last date to link PAN.",
        "The deadline expired on June 14, 2025.",
        "Applications closed on 14 June 2025.",
        "The installment was expected in July 2026 but was delayed.",
        "The last date had been 30 June 2025.",
    ],
)
def test_explicit_past_tense_is_historical_and_not_flagged(text):
    (m,) = analyze_text(text)
    assert m.role == TemporalRole.HISTORICAL_REFERENCE, (m.cue_text, m.clause)
    assert run_staleness([_result(text)], AS_OF).findings == []


@pytest.mark.parametrize(
    "text, role",
    [
        ("The deadline was extended to June 14, 2026.", TemporalRole.DEADLINE),  # nearest cue still wins
        ("December 31, 2019 is the last date to link PAN.", TemporalRole.DEADLINE),  # present tense
        ("The installment is expected in July 2026.", TemporalRole.EXPECTED_EVENT),
    ],
)
def test_present_tense_is_not_treated_as_narration(text, role):
    (m,) = analyze_text(text)
    assert m.role == role


def test_by_deadline_is_flagged_when_passed():
    out = run_staleness([_result("Complete the KYC process by September 30, 2024.", position=3)], AS_OF)
    (f,) = out.findings
    assert f.temporal_role == TemporalRole.DEADLINE and f.days_expired == 731
    assert f.strength == Strength.HIGH


# --- detector ----------------------------------------------------------------


def _result(snippet, title="", position=1, rtype=ResultType.ORGANIC, source_id="src_a", rid="res_1"):
    return SearchResult(
        id=rid,
        search_run_id="srch_1",
        source_id=source_id,
        engine="google",
        query="q",
        result_type=rtype,
        position=position,
        title=title,
        link="https://example.in/a",
        snippet=snippet,
        retrieved_at=datetime(2026, 10, 1, tzinfo=timezone.utc),
        data_mode=DataMode.MOCK,
    )


def test_severity_thresholds_and_config():
    assert severity(61, 5, StalenessConfig()) == Strength.HIGH
    assert severity(61, 6, StalenessConfig()) == Strength.MEDIUM
    assert severity(61, None, StalenessConfig()) == Strength.MEDIUM  # unranked never HIGH
    assert severity(15, 1, StalenessConfig()) == Strength.MEDIUM
    assert severity(14, 1, StalenessConfig()) == Strength.LOW
    assert severity(20, 1, StalenessConfig(high_days=10, medium_days=5)) == Strength.HIGH


def test_finding_carries_exact_evidence():
    snippet = "Intro text. The last date to apply is June 14, 2026. After this date a fee applies."
    out = run_staleness([_result(snippet, position=2)], AS_OF)
    (f,) = out.findings
    assert f.days_expired == 109 and f.strength == Strength.HIGH
    assert f.temporal_role == TemporalRole.DEADLINE
    assert f.source_ids == ["src_a"] and f.result_ids == ["res_1"]
    (span,) = [s for s in out.evidence_spans if s.id in f.evidence_span_ids]
    assert span.field == EvidenceField.SNIPPET
    assert span.text == "The last date to apply is June 14, 2026."
    assert snippet[span.start : span.end] == span.text  # offsets point into the original text
    assert "may be outdated" in f.explanation and "false" not in f.explanation.lower()


def test_title_and_snippet_repeat_is_one_claim_two_spans():
    title = "Free update Last Date June 14, 2026"
    out = run_staleness([_result(f"{title} | more text", title=title)], AS_OF)
    (claim,) = out.claims
    assert len(claim.evidence_span_ids) == 2
    assert len(out.findings) == 1


def test_expected_event_finding_wording():
    out = run_staleness([_result("The installment is expected in July 2026.")], AS_OF)
    (f,) = out.findings
    assert f.title == "Expected date has already passed"
    assert f.date_end == "2026-07-31" and f.days_expired == 62


@pytest.mark.parametrize(
    "snippet",
    [
        "Free update till 14 June 2027.",  # future deadline
        "The deadline is October 2026.",  # period not yet over
        "The previous deadline was June 14, 2025.",  # historical
        "Last Updated: April 07, 2026",  # publication stamp
        "Released on March 3, 2026.",  # past event
        "Rolling applications since June 2025, no deadline.",  # open-ended
    ],
)
def test_not_flagged(snippet):
    assert run_staleness([_result(snippet)], AS_OF).findings == []


def test_unknown_roles_are_kept_as_claims_but_not_flagged():
    out = run_staleness([_result("Premieres September 9, 2026 on TV.")], AS_OF)
    assert out.findings == []
    assert [c.role for c in out.claims] == [TemporalRole.UNKNOWN]


def test_same_source_two_results_two_events_one_source():
    snippet = "Last date: June 14, 2026."
    results = [
        _result(snippet, rid="res_1", rtype=ResultType.ANSWER_BOX, position=0),
        _result(snippet, rid="res_2", position=3),
    ]
    out = run_staleness(results, AS_OF)
    assert len(out.findings) == 2  # detection events
    assert {sid for f in out.findings for sid in f.source_ids} == {"src_a"}  # one source
    strengths = {f.result_ids[0]: f.strength for f in out.findings}
    assert strengths == {"res_1": Strength.MEDIUM, "res_2": Strength.HIGH}  # answer box is unranked
