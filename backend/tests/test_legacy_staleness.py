"""
Pytest port of the original test_offline.py cases, with real assertions,
run through the root staleness_detector.py interface (now backed by the
shared engine in backend/civicsignal/detectors/staleness/).

The regression tests at the bottom started as strict xfails documenting the
five Phase 0 defects; Phase 2 fixed all five (each XPASSed) and the markers
were removed so they now guard against regressions.
"""

from datetime import date

import pytest

from staleness_detector import analyze_result

TODAY = date(2026, 9, 29)  # date of the original research spike; injected, not hardcoded in logic


def _result(position, link, title, snippet):
    return {"position": position, "link": link, "title": title, "snippet": snippet}


CASE_1 = _result(
    2,
    "https://www.bajajfinserv.in/pm-kisan-23th-installment-date-2026",
    "PM Kisan 23rd Installment Date 2026 - Bajaj Finance",
    "The PM Kisan 23rd installment is expected to be released in June-July 2026, although "
    "the government has not yet announced an official date.",
)
CASE_2 = _result(
    3,
    "https://aimindia.in/uidai-aadhaar-2026-update/",
    "UIDAI Aadhaar 2026: Free Document Update Last Date",
    "The deadline for free online updates of Aadhaar details has been extended to June 14, 2026. "
    "After this date, any updates will require a fee of Rs 50.",
)
CASE_3 = _result(
    4,
    "https://razorpay.com/learn/aadhaar-card-update-last-date/",
    "Aadhaar Card Update 2026: Last Date and Steps to Update Online",
    "The deadline to update Aadhaar documents online for free is 14 June 2025 through the "
    "myAadhaar portal. After this date, you'll need to pay a fee.",
)
CASE_4 = _result(
    1,
    "https://sarkaribaba.com/govt-schemes/ayushman-bharat-card-apply-online/",
    "Ayushman Bharat Card 2026",
    "Method 1 - Apply online (self-eKYC via portal). Last Date: Open (rolling registrations - no deadline).",
)
CASE_5 = _result(
    5,
    "https://example.com/aadhaar-update-history",
    "Aadhaar Update Deadline History",
    "The previous Aadhaar deadline was June 14, 2025, before it was extended by UIDAI to the current date.",
)
CASE_6 = _result(
    1,
    "https://example.com/pm-kisan-range",
    "PM Kisan Update",
    "The 23rd installment is expected in June-July 2026, pending official confirmation.",
)
CASE_7 = _result(
    2,
    "https://example.com/aadhaar-history-2",
    "Aadhaar Deadline History",
    "The previous deadline was June 14, 2025, before UIDAI extended it further.",
)
CASE_8 = _result(
    3,
    "https://example.com/pm-kisan-future",
    "PM Kisan Next Installment",
    "The next installment is expected in October 2026, following the usual four-month cycle.",
)
CASE_9 = _result(
    5,
    "https://www.youtube.com/watch?v=wtnafTFIJzI",
    "Aadhaar Card Document Update FREE Last Date June 14, 2026",
    "Aadhaar Card Document Update FREE Last Date June 14, 2026 | Aadhaar Update Online Process 2026. "
    "Auto-dubbed. 7 likes318 ... Aadhaar Card Document Update FREE Last Date June 14, 2026",
)


def _only_claim(result):
    claims, _, _ = analyze_result(result, TODAY)
    assert len(claims) == 1, f"expected exactly one claim, got {len(claims)}"
    return claims[0]


def test_case1_expected_range_already_passed_is_flagged():
    c = _only_claim(CASE_1)
    assert (c.role, c.date_precision) == ("deadline", "month_range")
    assert (c.date_start, c.date_end) == ("2026-06-01", "2026-07-31")
    assert c.days_expired == 60
    assert c.severity == "medium"  # >14 days but not >60


def test_case2_extended_deadline_now_past_is_flagged():
    c = _only_claim(CASE_2)
    assert (c.role, c.date_precision, c.date_end) == ("deadline", "day", "2026-06-14")
    assert c.days_expired == 107
    assert c.severity == "high"  # >60 days and position <= 5


def test_case3_old_deadline_is_flagged():
    c = _only_claim(CASE_3)
    assert c.date_end == "2025-06-14"
    assert c.days_expired == 472
    assert c.severity == "high"


def test_case4_open_ended_is_not_flagged():
    claims, exclusions, _ = analyze_result(CASE_4, TODAY)
    assert claims == []


@pytest.mark.parametrize("case", [CASE_5, CASE_7], ids=["case5", "case7"])
def test_historical_reference_is_not_flagged(case):
    claims, exclusions, _ = analyze_result(case, TODAY)
    assert claims == []
    assert exclusions["historical"] == 1


def test_case6_month_range_end_passed_is_flagged():
    c = _only_claim(CASE_6)
    assert c.date_precision == "month_range"
    assert c.date_end == "2026-07-31"
    assert c.days_expired == 60


def test_case8_future_month_is_not_flagged():
    claims, _, _ = analyze_result(CASE_8, TODAY)
    assert claims == []


def test_case9_title_repeated_in_snippet_yields_one_claim():
    c = _only_claim(CASE_9)
    assert c.date_end == "2026-06-14"


def test_full_snippet_is_kept_for_short_snippets():
    c = _only_claim(CASE_2)
    assert c.snippet.startswith(CASE_2["snippet"])


# --- Regression tests for the five Phase 0 defects (fixed in Phase 2). ---


def _flagged_dates(snippet, position=1):
    claims, _, _ = analyze_result(_result(position, "https://x.in/a", "", snippet), TODAY)
    return sorted(c.date_end for c in claims)


# Phase 0 bug (fixed): 'till'/'until' not recognized as a deadline cue
def test_fixed_till_is_a_deadline_cue():
    assert _flagged_dates("UIDAI is Offering Free Aadhaar Update Till 14 June 2026.") == ["2026-06-14"]


# Phase 0 bug (fixed): role taken from whole window, not the nearest cue
def test_fixed_nearest_cue_wins():
    snippet = "Previous deadline was June 14, 2025. Current deadline is June 14, 2026."
    assert _flagged_dates(snippet) == ["2026-06-14"]


# Phase 0 bug (fixed): cue from another clause attaches to the wrong date
def test_fixed_cross_clause_cue_bleed():
    snippet = "The results were released on March 3, 2026, and the deadline is June 14, 2026."
    assert _flagged_dates(snippet) == ["2026-06-14"]


# Phase 0 bug (fixed): ISO dates (2026-06-14) are not extracted
def test_fixed_iso_date():
    assert _flagged_dates("The last date is 2026-06-14.") == ["2026-06-14"]


# Phase 0 bug (fixed): Dec-Jan range produces date_end before date_start
def test_fixed_year_wrapping_range():
    from date_extraction import extract_date_claims

    (claim,) = extract_date_claims("Counselling expected in December-January 2026.")
    assert claim.precision == "month_range"
    assert (claim.date_start.isoformat(), claim.date_end.isoformat()) == ("2025-12-01", "2026-01-31")
