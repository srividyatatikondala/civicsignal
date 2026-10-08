"""
Temporal role classification for a single date mention.

Phase 2 replaces "first rule that matches anywhere in a 60-char window"
with:
  1. Clause scoping — the text is split into clauses (sentences, "|", "·",
     ";", ellipses, and ", and"/", but"/", while" joins). Only cues in the
     same clause as the date are considered, so cues cannot bleed across
     clauses.
  2. Nearest cue wins — every cue in the clause is located; the one closest
     to the date is chosen. Cues after the date are allowed ("December 31,
     2019 is the last date") but carry a small distance penalty, because
     most cues precede the date they govern.
  3. Specific beats generic — when one cue span contains another, the longer
     one is kept ("previous deadline" is historical, not a deadline).

Historical markers are only cues when they modify a date noun ("previous
deadline", "earlier date") or are temporal adverbs ("previously",
"extended from"). A bare "previous" ("based on previous payment patterns")
is not about the date and is ignored.

Explicit past-tense framing of a deadline or expectation ("the deadline was",
"was the last date", "expired on", "was expected in") is treated as
historical narration: the text itself says the date is in the past, so it is
not presenting a stale date as current. Ambiguous tense ("CBDT set
December 31, 2025 as the deadline" — "set" is both past and present) cannot
be resolved by these rules and remains a known limitation.

Adjacent-only cues ("by", "before", "on or before", "no later than") are
too generic to govern a date from a distance ("set by CBDT ... June 2025"),
so they count only when they directly precede the date.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ...models import TemporalRole

FOLLOWING_CUE_PENALTY = 15  # chars added to the distance of cues that come after the date
MAX_CUE_DISTANCE = 80  # cues farther than this (after penalty) are ignored -> unknown
MAX_ADJACENT_GAP = 2  # adjacent-only cues must be within this many chars before the date


@dataclass(frozen=True)
class Cue:
    role: TemporalRole
    text: str
    start: int
    end: int
    adjacent_only: bool = False


@dataclass(frozen=True)
class RoleDecision:
    role: TemporalRole
    cue: Cue | None
    clause_start: int
    clause_end: int


_DATE_NOUN = r"(?:deadline|last\s+date|due\s+date|end\s+date|date|schedule)"

_DEADLINE_NOUN = r"(?:deadline|last\s+date|due\s+date)"

# (role, pattern, adjacent_only). Ordered by tie-break priority (first wins when equally near).
CUE_PATTERNS: list[tuple[TemporalRole, re.Pattern, bool]] = [
    (
        # Date-of-birth cutoffs ("born on or before 31 December 2008") are eligibility rules,
        # not deadlines. Listed first so they outrank the generic "before" cue.
        TemporalRole.ELIGIBILITY_CUTOFF,
        re.compile(
            r"\b(?:born\s+(?:on\s+or\s+)?(?:before|after|between)|date\s+of\s+birth|dob)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        TemporalRole.HISTORICAL_REFERENCE,
        re.compile(
            rf"\b(?:previous|earlier|old|original|prior|last\s+year'?s?)\b(?:\s+[\w-]+){{0,2}}?\s+{_DATE_NOUN}\b"
            r"|\b(?:previously|originally|was\s+earlier|used\s+to\s+be|(?:was\s+)?extended\s+from|last\s+year)\b"
            # explicit past-tense framing of a deadline / expectation
            rf"|\b{_DEADLINE_NOUN}\s+(?:was|had\s+been|expired|ended|lapsed|passed)\b"
            rf"|\b(?:was|were|had\s+been)\s+(?:the\s+)?(?:final\s+)?{_DEADLINE_NOUN}\b"
            r"|\b(?:expired|ended|lapsed|closed)\s+on\b"
            r"|\b(?:was|were|had\s+been)\s+(?:expected|scheduled)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        TemporalRole.OPEN_ENDED,
        re.compile(
            r"\b(?:rolling|ongoing|no\s+deadline|open\s+always|any\s?time|open[- ]ended|throughout\s+the\s+year)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        # Page publication/update stamps ("Last Updated: April 07, 2026") are not claims about events.
        TemporalRole.PUBLICATION,
        re.compile(r"\b(?:last\s+updated|updated\s+on|published(?:\s+on)?)\b", re.IGNORECASE),
        False,
    ),
    (
        TemporalRole.DEADLINE,
        re.compile(
            r"\b(?:last\s+date|deadline|closes?\s+on|close[sd]?\s+by|"
            r"extended\s+(?:to|till|until)|apply\s+before|valid\s+(?:till|until)|"
            r"due\s+(?:on|by)|open\s+till|applications?\s+(?:open|close|end)|till|until)\b",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        # Generic prepositions: only meaningful when they directly precede the date.
        TemporalRole.DEADLINE,
        re.compile(r"\b(?:on\s+or\s+before|no\s+later\s+than|by|before)\b", re.IGNORECASE),
        True,
    ),
    (
        # Original prototype treated these as "deadline"; they are kept as a separate role so
        # findings can say "expected date has passed" rather than "deadline expired".
        TemporalRole.EXPECTED_EVENT,
        re.compile(r"\b(?:expected|scheduled|will\s+be\s+(?:released|announced))\b", re.IGNORECASE),
        False,
    ),
    (
        TemporalRole.PAST_EVENT,
        re.compile(
            # (?<!be\s) keeps "will be released on" from also matching as a past event
            r"(?<!be\s)\b(?:released\s+on|announced\s+on|disbursed\s+on|completed\s+on|held\s+on|"
            r"launched\s+on|signed\s+on)\b"
            r"|\b(?:was\s+released|took\s+place|effective\s+from|live\s+since|rolled\s+out)\b",
            re.IGNORECASE,
        ),
        False,
    ),
]

_PRIORITY: dict[TemporalRole, int] = {}
for _i, (_role, _, _) in enumerate(CUE_PATTERNS):
    _PRIORITY.setdefault(_role, _i)

# --- clause segmentation -----------------------------------------------------

_BOUNDARY = re.compile(
    r"(?:[.!?](?=\s+(?![a-z])))"  # sentence end, unless the next word starts lowercase
    r"|(?:\s*[|·•;]\s*)"  # separators
    r"|(?:\.{3}|…)"  # ellipses (snippet truncation)
    r"|(?:,(?=\s+(?:and|but|while|whereas)\s))",  # joined clauses: cut after the comma
)
_ABBREVIATIONS = {
    "rs", "no", "dr", "mr", "mrs", "ms", "st", "vs", "govt", "dept", "approx", "e.g", "i.e", "etc",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
}


def clause_bounds(text: str, protected: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Split text into (start, end) clause spans. Never splits inside a protected span (dates)."""
    cuts = [0]
    for m in _BOUNDARY.finditer(text):
        pos = m.start()
        if any(s <= pos < e for s, e in protected):
            continue
        if m.group(0) == ".":
            word = re.search(r"([A-Za-z.]+)$", text[:pos])
            if word:
                w = word.group(1).lower().rstrip(".")
                # abbreviations and initials ("Rs.", "Sept.", "U.S.") do not end a clause
                if w in _ABBREVIATIONS or len(w.split(".")[-1]) == 1:
                    continue
        cuts.append(m.end())
    cuts.append(len(text))
    spans = []
    for a, b in zip(cuts, cuts[1:]):
        if b > a:
            spans.append((a, b))
    return spans


def find_cues(text: str, start: int, end: int) -> list[Cue]:
    """All cues within text[start:end], with contained (less specific) matches removed."""
    segment = text[start:end]
    cues = [
        Cue(role, m.group(0), start + m.start(), start + m.end(), adjacent_only)
        for role, pattern, adjacent_only in CUE_PATTERNS
        for m in pattern.finditer(segment)
    ]
    return [
        c
        for c in cues
        if not any(
            o is not c and o.start <= c.start and c.end <= o.end and (o.end - o.start) > (c.end - c.start)
            for o in cues
        )
    ]


def _distance(cue: Cue, date_start: int, date_end: int) -> int:
    if cue.end <= date_start:
        return date_start - cue.end
    if cue.start >= date_end:
        return cue.start - date_end + FOLLOWING_CUE_PENALTY
    return 0  # overlapping


def classify_date_role(
    text: str, date_start: int, date_end: int, protected: list[tuple[int, int]] | None = None
) -> RoleDecision:
    """Role of the date at text[date_start:date_end], from the nearest cue in its clause."""
    protected = protected or [(date_start, date_end)]
    clause = next(
        ((a, b) for a, b in clause_bounds(text, protected) if a <= date_start < b),
        (0, len(text)),
    )
    candidates = [
        c
        for c in find_cues(text, *clause)
        if not (c.start < date_end and date_start < c.end)  # a cue can't be inside the date itself
        and (not c.adjacent_only or 0 <= date_start - c.end <= MAX_ADJACENT_GAP)
    ]
    scored = [(_distance(c, date_start, date_end), _PRIORITY[c.role], c) for c in candidates]
    scored = [s for s in scored if s[0] <= MAX_CUE_DISTANCE]
    if not scored:
        return RoleDecision(TemporalRole.UNKNOWN, None, *clause)
    _, _, best = min(scored, key=lambda s: (s[0], s[1]))
    return RoleDecision(best.role, best, *clause)
