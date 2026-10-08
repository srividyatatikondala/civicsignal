"""
Offline validation for the staleness detector's core logic, using REAL
snippet text captured during today's manual research spikes (not synthetic
examples). This doesn't touch the network — it calls analyze_result()
directly with hand-built SerpApi-shaped result dicts.

Run: python test_offline.py
"""

from datetime import date

from staleness_detector import analyze_result

TODAY = date(2026, 9, 29)  # matches today's actual date

# Real snippet text from Bajaj Finance's PM Kisan page (found today) —
# should be flagged: "expected" language about an installment that has
# since actually happened.
CASE_1 = {
    "position": 2,
    "link": "https://www.bajajfinserv.in/pm-kisan-23th-installment-date-2026",
    "title": "PM Kisan 23rd Installment Date 2026 - Bajaj Finance",
    "snippet": (
        "The PM Kisan 23rd installment is expected to be released in "
        "June-July 2026, although the government has not yet announced "
        "an official date."
    ),
}

# Real snippet from aimindia.in's Aadhaar page — the actual deadline is
# genuinely current (June 14, 2026, still in the future relative to
# TODAY=Sept 2026 would make this ALREADY PASSED -> should flag).
CASE_2 = {
    "position": 3,
    "link": "https://aimindia.in/uidai-aadhaar-2026-update/",
    "title": "UIDAI Aadhaar 2026: Free Document Update Last Date",
    "snippet": (
        "The deadline for free online updates of Aadhaar details has "
        "been extended to June 14, 2026. After this date, any updates "
        "will require a fee of Rs 50."
    ),
}

# Real snippet from Razorpay's Aadhaar page — a genuinely, badly stale
# deadline claim (June 14, 2025), the strongest real hit from the spike.
CASE_3 = {
    "position": 4,
    "link": "https://razorpay.com/learn/aadhaar-card-update-last-date/",
    "title": "Aadhaar Card Update 2026: Last Date and Steps to Update Online",
    "snippet": (
        "The deadline to update Aadhaar documents online for free is "
        "14 June 2025 through the myAadhaar portal. After this date, "
        "you'll need to pay a fee."
    ),
}

# Should NOT be flagged: open-ended, no real deadline.
CASE_4 = {
    "position": 1,
    "link": "https://sarkaribaba.com/govt-schemes/ayushman-bharat-card-apply-online/",
    "title": "Ayushman Bharat Card 2026",
    "snippet": (
        "Method 1 - Apply online (self-eKYC via portal). "
        "Last Date: Open (rolling registrations - no deadline)."
    ),
}

# The exact case Vidya flagged as a required negative test: a historical
# reference to a past, already-superseded deadline. Must NOT be flagged,
# even though the date itself is in the past, because the sentence is
# explicitly framing it as retired, not as a live claim.
CASE_5 = {
    "position": 5,
    "link": "https://example.com/aadhaar-update-history",
    "title": "Aadhaar Update Deadline History",
    "snippet": (
        "The previous Aadhaar deadline was June 14, 2025, before it "
        "was extended by UIDAI to the current date."
    ),
}


def run_case(name, result_dict, expect_flagged: bool):
    claims, exclusions, excluded_samples = analyze_result(result_dict, TODAY)
    flagged = len(claims) > 0
    status = "PASS" if flagged == expect_flagged else "FAIL"
    print(
        f"[{status}] {name}: expected_flagged={expect_flagged}, got_flagged={flagged}"
    )
    for c in claims:
        print(
            f"    -> role={c.role} precision={c.date_precision} "
            f"start={c.date_start} end={c.date_end} "
            f"days_expired={c.days_expired} severity={c.severity}"
        )
        print(f"       context: ...{c.context}...")
    if (
        exclusions["historical"]
        or exclusions["open_ended"]
        or exclusions["unknown_role"]
    ):
        print(f"    excluded: {exclusions}")
    for e in excluded_samples:
        print(f"       [unknown-role sample] context: ...{e.context}...")
    print()


# --- New cases: exactly the three scenarios specified for the range-date fix ---

# "23rd installment expected in June-July 2026" — range, end month (July)
# has fully passed by TODAY (Sept 29 2026) -> SHOULD flag.
CASE_6 = {
    "position": 1,
    "link": "https://example.com/pm-kisan-range",
    "title": "PM Kisan Update",
    "snippet": "The 23rd installment is expected in June-July 2026, pending official confirmation.",
}

# "The previous deadline was June 14, 2025" -> historical_reference,
# must NOT flag even though the date itself is in the past.
CASE_7 = {
    "position": 2,
    "link": "https://example.com/aadhaar-history-2",
    "title": "Aadhaar Deadline History",
    "snippet": "The previous deadline was June 14, 2025, before UIDAI extended it further.",
}

# "The next installment is expected in October 2026" -> month precision,
# October has NOT arrived yet relative to TODAY (Sept 29 2026) -> must NOT flag.
CASE_8 = {
    "position": 3,
    "link": "https://example.com/pm-kisan-future",
    "title": "PM Kisan Next Installment",
    "snippet": "The next installment is expected in October 2026, following the usual four-month cycle.",
}


# The exact bug found in the live Aadhaar run: SerpApi's snippet for a
# YouTube result already contains the title text verbatim. Concatenating
# title unconditionally used to double-extract the same date claim.
CASE_9 = {
    "position": 5,
    "link": "https://www.youtube.com/watch?v=wtnafTFIJzI",
    "title": "Aadhaar Card Document Update FREE Last Date June 14, 2026",
    "snippet": (
        "Aadhaar Card Document Update FREE Last Date June 14, 2026 | "
        "Aadhaar Update Online Process 2026. Auto-dubbed. 7 likes318 ... "
        "Aadhaar Card Document Update FREE Last Date June 14, 2026"
    ),
}


if __name__ == "__main__":
    print(f"Running offline validation as of TODAY={TODAY}\n")
    run_case("Case 1: PM Kisan 'expected' language, event already passed", CASE_1, True)
    run_case("Case 2: Aadhaar 'extended to June 14 2026' (now past)", CASE_2, True)
    run_case("Case 3: Razorpay stale deadline June 2025", CASE_3, True)
    run_case("Case 4: Ayushman Bharat open-ended, should NOT flag", CASE_4, False)
    run_case("Case 5: historical reference, should NOT flag", CASE_5, False)
    print("--- Range-date fix: the three specified scenarios ---\n")
    run_case(
        "Case 6: 'June-July 2026' range, end month has passed -> flag", CASE_6, True
    )
    run_case(
        "Case 7: 'previous deadline was June 14 2025' -> DO NOT flag", CASE_7, False
    )
    run_case(
        "Case 8: 'expected in October 2026', hasn't arrived -> DO NOT flag",
        CASE_8,
        False,
    )
    print("--- Regression test: live-found duplicate-title bug ---\n")
    claims, _, _ = analyze_result(CASE_9, TODAY)
    status = "PASS" if len(claims) == 1 else "FAIL"
    print(
        f"[{status}] Case 9: title repeated inside snippet -> exactly 1 claim, "
        f"got {len(claims)}\n"
    )
