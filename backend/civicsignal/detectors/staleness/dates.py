"""
Precision-aware date/range extraction (ported from the root prototype's
date_extraction.py; the root module now re-exports this one).

Regex-first and explicit about what kind of date claim was found:

  - "day"         -> exact date: "14 June 2026", "June 14, 2026", "14/06/2026", "2026-06-14"
  - "month"       -> a single month: "July 2026" (no day is invented)
  - "month_range" -> a month-to-month span: "June-July 2026", "December-January 2026"

date_start/date_end bound the claimed period, so callers can ask "has the
whole period elapsed?" without pretending the source named a specific day.

Phase 2 fixes:
  * ISO dates (2026-06-14) are recognized.
  * Year-wrapping ranges ("December-January 2026") no longer invert: the
    stated year is attached to the later month, so the range is
    Dec 2025 - Jan 2026. (Assumption, documented in docs/methodology.md.)
  * Numeric DD/MM/YYYY dates where both readings are valid (05/06/2026)
    are marked ambiguous=True instead of silently trusted.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTH_NAMES = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
MONTH_PATTERN = "|".join(sorted(MONTH_NAMES.keys(), key=len, reverse=True))


@dataclass
class DateClaim:
    matched_text: str
    start_idx: int
    end_idx: int
    precision: str  # "day" | "month" | "month_range"
    date_start: date
    date_end: date
    ambiguous: bool = False


def _last_day_of_month(year: int, month: int) -> int:
    return calendar.monthrange(year, month)[1]


_MONTH_RANGE = re.compile(
    rf"\b({MONTH_PATTERN})\.?\s*(?:[-–—]|to)\s*({MONTH_PATTERN})\.?\s+(\d{{4}})\b",
    re.IGNORECASE,
)

# (pattern, group order) — order: "dmy" | "mdy" | "ymd" | "numeric_dmy"
_FULL_DATE_PATTERNS: list[tuple[re.Pattern, str]] = [
    # 2026-06-14 (ISO). Checked first so its digits are not re-read by other patterns.
    (re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b"), "ymd"),
    # 14 June 2026 / 14 June, 2026
    (re.compile(rf"\b(\d{{1,2}})\s+({MONTH_PATTERN})\.?,?\s+(\d{{4}})\b", re.IGNORECASE), "dmy"),
    # June 14, 2026 / June 14 2026
    (re.compile(rf"\b({MONTH_PATTERN})\.?\s+(\d{{1,2}}),?\s+(\d{{4}})\b", re.IGNORECASE), "mdy"),
    # 14/06/2026 (DD/MM/YYYY, India convention)
    (re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"), "numeric_dmy"),
]

_MONTH_ONLY = re.compile(rf"\b({MONTH_PATTERN})\.?\s+(\d{{4}})\b", re.IGNORECASE)


def _spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def extract_date_claims(text: str) -> list[DateClaim]:
    """
    Extract date claims, most-specific pattern first, so a month range
    consumes its span before the month-only pattern can re-match either half.
    """
    claims: list[DateClaim] = []
    consumed: list[tuple[int, int]] = []

    def is_free(start: int, end: int) -> bool:
        return not any(_spans_overlap(start, end, s, e) for s, e in consumed)

    # 1. month ranges
    for m in _MONTH_RANGE.finditer(text):
        start, end = m.start(), m.end()
        if not is_free(start, end):
            continue
        month_a = MONTH_NAMES[m.group(1).lower()]
        month_b = MONTH_NAMES[m.group(2).lower()]
        year = int(m.group(3))
        year_a = year - 1 if month_b < month_a else year  # "December-January 2026" -> Dec 2025
        date_start = date(year_a, month_a, 1)
        date_end = date(year, month_b, _last_day_of_month(year, month_b))
        claims.append(DateClaim(m.group(0), start, end, "month_range", date_start, date_end))
        consumed.append((start, end))

    # 2. day-precision dates
    for pattern, order in _FULL_DATE_PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if not is_free(start, end):
                continue
            g = m.groups()
            ambiguous = False
            try:
                if order == "ymd":
                    year, month, day = int(g[0]), int(g[1]), int(g[2])
                elif order == "dmy":
                    day, month, year = int(g[0]), MONTH_NAMES[g[1].lower()], int(g[2])
                elif order == "mdy":
                    month, day, year = MONTH_NAMES[g[0].lower()], int(g[1]), int(g[2])
                else:  # numeric_dmy
                    day, month, year = int(g[0]), int(g[1]), int(g[2])
                    ambiguous = day <= 12 and month <= 12 and day != month
                d = date(year, month, day)
            except (ValueError, KeyError):
                continue  # invalid date -> skip, don't guess
            claims.append(DateClaim(m.group(0), start, end, "day", d, d, ambiguous))
            consumed.append((start, end))

    # 3. month-only (least specific)
    for m in _MONTH_ONLY.finditer(text):
        start, end = m.start(), m.end()
        if not is_free(start, end):
            continue
        month = MONTH_NAMES[m.group(1).lower()]
        year = int(m.group(2))
        claims.append(
            DateClaim(
                m.group(0), start, end, "month",
                date(year, month, 1), date(year, month, _last_day_of_month(year, month)),
            )
        )
        consumed.append((start, end))

    claims.sort(key=lambda c: c.start_idx)
    return claims
