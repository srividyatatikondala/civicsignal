"""
Deterministic Indian-rupee amount extraction.

Recognized: "₹2,000", "Rs. 10/-", "Rs 50", "INR 1000", "1,000 rupees",
"₹2650 Crore", "Rs 5,653 crore", "₹3,590 Cr", "₹1.5 lakh". A currency
marker is required — bare numbers are never treated as money.

A preceding qualifier turns the value into an interval:
  about/around/approximately/approx/roughly  -> [0.9x, 1.1x]
  nearly/almost                               -> [0.9x, x]
  over/more than/above/at least/minimum of    -> [x, unbounded)
  up to/upto/less than/under/maximum of       -> [0, x]

Crore/lakh/million/billion amounts are marked aggregate: they are usually
programme totals (per scheme, per installment, per phase) whose scope is
rarely stated precisely in a snippet, so they are extracted as claims but
not compared for conflicts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SCALES = {
    "crore": 1e7, "crores": 1e7, "cr": 1e7,
    "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "million": 1e6, "billion": 1e9,
    "thousand": 1e3,
}
_AGGREGATE_SCALES = {"crore", "lakh", "million", "billion"}

_NUMBER = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_SCALE = r"(?:crores?|cr|lakhs?|lacs?|million|billion|thousand)\b"
_QUALIFIER = (
    r"(?:about|around|approximately|approx\.?|roughly|nearly|almost|over|more\s+than|above|"
    r"at\s+least|minimum\s+of|up\s?to|less\s+than|under|maximum\s+of)"
)

_PREFIXED = re.compile(
    rf"(?:(?P<q1>{_QUALIFIER})\s+)?(?:₹|\bRs\.?|\bINR)\s?(?P<n1>{_NUMBER})(?:\s?(?P<s1>{_SCALE}))?(?:\s?/-)?",
    re.IGNORECASE,
)
_SUFFIXED = re.compile(
    rf"(?:(?P<q2>{_QUALIFIER})\s+)?\b(?P<n2>{_NUMBER})(?:\s?(?P<s2>{_SCALE}))?\s+rupees\b",
    re.IGNORECASE,
)

_INTERVALS = {
    "about": (0.9, 1.1), "around": (0.9, 1.1), "approximately": (0.9, 1.1), "approx": (0.9, 1.1),
    "approx.": (0.9, 1.1), "roughly": (0.9, 1.1),
    "nearly": (0.9, 1.0), "almost": (0.9, 1.0),
    "over": (1.0, None), "more than": (1.0, None), "above": (1.0, None), "at least": (1.0, None),
    "minimum of": (1.0, None),
    "up to": (0.0, 1.0), "upto": (0.0, 1.0), "less than": (0.0, 1.0), "under": (0.0, 1.0),
    "maximum of": (0.0, 1.0),
}


@dataclass
class AmountMatch:
    matched_text: str
    start: int
    end: int
    value: float
    low: float
    high: float | None
    qualifier: str | None
    scale: str | None
    aggregate: bool


_CANONICAL_SCALE = {
    "crore": "crore", "crores": "crore", "cr": "crore",
    "lakh": "lakh", "lakhs": "lakh", "lac": "lakh", "lacs": "lakh",
    "million": "million", "billion": "billion", "thousand": "thousand",
}


def _scale_key(raw: str | None) -> str | None:
    return _CANONICAL_SCALE.get(raw.lower()) if raw else None


def extract_amounts(text: str) -> list[AmountMatch]:
    matches: list[AmountMatch] = []
    taken: list[tuple[int, int]] = []
    for pattern, (q, n, s) in ((_PREFIXED, ("q1", "n1", "s1")), (_SUFFIXED, ("q2", "n2", "s2"))):
        for m in pattern.finditer(text):
            if any(a < m.end() and m.start() < b for a, b in taken):
                continue
            number = float(m.group(n).replace(",", ""))
            scale = _scale_key(m.group(s))
            value = number * (_SCALES[scale] if scale else 1.0)
            qualifier = " ".join(m.group(q).lower().split()) if m.group(q) else None
            lo_f, hi_f = _INTERVALS.get(qualifier or "", (1.0, 1.0))
            matches.append(
                AmountMatch(
                    matched_text=m.group(0).strip(),
                    start=m.start(),
                    end=m.end(),
                    value=value,
                    low=value * lo_f,
                    high=None if hi_f is None else value * hi_f,
                    qualifier=qualifier,
                    scale=scale,
                    aggregate=scale in _AGGREGATE_SCALES,
                )
            )
            taken.append((m.start(), m.end()))
    matches.sort(key=lambda a: a.start)
    return matches


def format_inr(value: float) -> str:
    """Indian-style display: 2000 -> '₹2,000'; 4e10 -> '₹4,000 crore'."""
    if value >= 1e7 and value % 1e5 == 0:
        return f"₹{value / 1e7:,.2f}".rstrip("0").rstrip(".") + " crore"
    if value >= 1e5 and value % 1e3 == 0:
        return f"₹{value / 1e5:,.2f}".rstrip("0").rstrip(".") + " lakh"
    return f"₹{value:,.2f}".rstrip("0").rstrip(".")
