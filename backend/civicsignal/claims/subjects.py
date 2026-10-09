"""
Deterministic subject qualifiers and amount attributes.

Every claim in an investigation is assumed to be about the investigation's
topic (the user's query). Qualifiers narrow that topic so that claims about
different things are never compared:

  installment:<n>   "23rd installment", "second Rythu Bharosa installment",
                    "2nd phase", "installment 3"   (installment/instalment/
                    kist/tranche/phase are treated as the same unit)
  round:<n>         "round 3", "3rd round"
  population:<w>    children, minors, students, senior citizens, pensioners, NRIs, women
  age:<a>-<b>       "aged 5-17", "aged 5 to 17"

For each kind, the qualifier nearest to the claim's value within the same
clause is used. If the clause has no installment/round ordinal, a single
unambiguous ordinal in the result's title is used instead.

Subject key = sorted qualifiers joined by "|" ("" = the topic itself). Only
claims with identical subject keys are compared — when in doubt, claims are
kept apart rather than forced into a conflict.
"""

from __future__ import annotations

import re

_WORD_ORDINALS = {
    "first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6,
    "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10, "eleventh": 11, "twelfth": 12,
}
_UNITS = {
    "installment": "installment", "installments": "installment", "instalment": "installment",
    "instalments": "installment", "kist": "installment", "kisht": "installment",
    "tranche": "installment", "phase": "installment", "round": "round",
}
_UNIT = r"(?:installments?|instalments?|kisht?|tranche|phase|round)"
_ORD = rf"(?:\d{{1,3}}(?:st|nd|rd|th)|{'|'.join(_WORD_ORDINALS)})"

# "23rd installment", "second Rythu Bharosa installment", "23rd and 24th installments"
_ORDINAL_BEFORE = re.compile(
    rf"\b(?P<ords>{_ORD}(?:\s*(?:,|and|&|/|or)\s*{_ORD})*)\s+(?:(?!{_ORD}\b)[\w#-]+\s+){{0,3}}?(?P<unit>{_UNIT})\b",
    re.IGNORECASE,
)
_ORD_TOKEN = re.compile(_ORD, re.IGNORECASE)
AMBIGUOUS = "ambiguous"
_ORDINAL_AFTER = re.compile(rf"\b(?P<unit>{_UNIT})[\s#:-]*(?P<num>\d{{1,3}})\b", re.IGNORECASE)
_POPULATION = re.compile(
    r"\b(?P<pop>children|child|kids|minors|students|senior\s+citizens|pensioners|nris?|women)\b", re.IGNORECASE
)
_AGE = re.compile(r"\baged?\s+(?P<a>\d{1,3})\s*(?:[-–]|to)\s*(?P<b>\d{1,3})\b", re.IGNORECASE)

_POP_CANON = {"child": "children", "kids": "children", "nri": "nris"}


def _ordinal_value(token: str) -> int | None:
    token = token.lower()
    if token in _WORD_ORDINALS:
        return _WORD_ORDINALS[token]
    digits = re.match(r"\d+", token)
    return int(digits.group(0)) if digits else None


def _qualifier_matches(text: str, start: int, end: int) -> list[tuple[str, str, int, int]]:
    """(kind, label, span_start, span_end) for every qualifier inside text[start:end]."""
    segment = text[start:end]
    found = []
    for m in _ORDINAL_BEFORE.finditer(segment):
        unit = _UNITS[m.group("unit").lower()]
        for token in _ORD_TOKEN.findall(m.group("ords")):
            n = _ordinal_value(token)
            if n:
                found.append((unit, f"{unit}:{n}", start + m.start(), start + m.end()))
    for m in _ORDINAL_AFTER.finditer(segment):
        unit = _UNITS[m.group("unit").lower()]
        found.append((unit, f"{unit}:{int(m.group('num'))}", start + m.start(), start + m.end()))
    for m in _POPULATION.finditer(segment):
        word = " ".join(m.group("pop").lower().split())
        found.append(("population", f"population:{_POP_CANON.get(word, word)}", start + m.start(), start + m.end()))
    for m in _AGE.finditer(segment):
        found.append(("age", f"age:{int(m.group('a'))}-{int(m.group('b'))}", start + m.start(), start + m.end()))
    return found


def _gap(span: tuple[int, int], value: tuple[int, int]) -> int:
    if span[1] <= value[0]:
        return value[0] - span[1]
    if span[0] >= value[1]:
        return span[0] - value[1]
    return 0


def subject_qualifiers(
    text: str, clause: tuple[int, int], value_span: tuple[int, int], title: str = ""
) -> list[str]:
    """
    Qualifiers for a value at text[value_span], nearest per kind within its
    clause. A tie between different labels of one kind ("23rd and 24th
    installments") yields "<kind>:ambiguous" — such claims are not compared.
    """
    nearest: dict[str, tuple[int, set[str]]] = {}
    for kind, label, s, e in _qualifier_matches(text, *clause):
        d = _gap((s, e), value_span)
        if kind not in nearest or d < nearest[kind][0]:
            nearest[kind] = (d, {label})
        elif d == nearest[kind][0]:
            nearest[kind][1].add(label)
    if "installment" not in nearest and "round" not in nearest and title:
        title_ordinals = {label for kind, label, _, _ in _qualifier_matches(title, 0, len(title))
                          if kind in ("installment", "round")}
        if len(title_ordinals) == 1:
            label = next(iter(title_ordinals))
            nearest[label.split(":")[0]] = (0, {label})
    return sorted(
        next(iter(labels)) if len(labels) == 1 else f"{kind}:{AMBIGUOUS}"
        for kind, (_, labels) in nearest.items()
    )


def is_ambiguous(qualifiers: list[str]) -> bool:
    """True if the subject could not be pinned down; such claims are never compared."""
    return any(q.endswith(f":{AMBIGUOUS}") or q == ENTITY_UNRESOLVED for q in qualifiers)


# --- topic anchoring ------------------------------------------------------------------
#
# A claim is only assumed to be about the investigated topic when that is
# justified by the text:
#   1. its clause mentions a query anchor term            -> topic (no entity qualifier)
#   2. its clause names another entity and no anchor      -> "entity:<name>" (own subject)
#   3. its clause names nothing, but the result mentions
#      another entity (round-up / multi-topic page)       -> "entity:unresolved" (never compared)
#   4. otherwise                                          -> topic
#
# Anchors are the query's distinctive words; other entities are acronyms such as
# EPFO / CBDT / UIDAI. When the clause mentions the topic, an authority acronym
# beside it ("UIDAI free Aadhaar update") does not change the subject.

ENTITY_UNRESOLVED = "entity:unresolved"

_GENERIC_QUERY_WORDS = {
    "a", "an", "the", "of", "for", "in", "on", "to", "and", "or", "is", "are", "what", "when", "how",
    "which", "will", "be", "with", "by", "from", "my", "your",
    "date", "dates", "last", "next", "new", "latest", "free", "update", "updates", "updated",
    "deadline", "installment", "instalment", "installments", "kist", "exam", "link", "linking",
    "apply", "application", "online", "status", "fee", "fees", "charges", "scheme", "yojana",
    "government", "govt", "central", "state", "india", "indian", "release", "released", "amount",
    "list", "check", "card", "form", "result", "results", "notification", "schedule", "validity",
    "adults", "eligibility", "documents", "process", "registration", "payment", "money", "time",
}
# Upper-case tokens that are ordinary words or units in shouty titles, not entities.
_NON_ENTITY_UPPERCASE = {
    "FREE", "NEW", "LIVE", "OUT", "NOW", "BIG", "UPDATE", "DATE", "LAST", "OFFICIAL", "NEWS", "ALERT",
    "TODAY", "RESULT", "LINK", "PDF", "FAQ", "FAQS", "OTP", "KYC", "EKYC", "DBT", "UTC", "IST", "AM",
    "PM", "ID", "OK", "TV", "CARD", "DOCUMENT", "PAYMENT", "STATUS", "CHECK", "APPLY", "ONLINE",
    "VIDEO", "FULL", "HINDI", "ENGLISH", "II", "III", "IV", "VI", "RS", "INR", "USA", "UK", "FY",
}
_ACRONYM = re.compile(r"(?<![\w-])([A-Z][A-Z0-9]{1,5})(?![\w-])")
_WORD = re.compile(r"[a-z0-9]+")


def _split_compound(text: str) -> str:
    """'#RythuBharosa' -> 'Rythu Bharosa'; 'PM-KISAN' -> 'PM KISAN'."""
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return re.sub(r"[-_#/]", " ", text)


def query_anchors(query: str) -> set[str]:
    """Distinctive lower-case words of the user's query (generic civic words and numbers removed)."""
    words = _WORD.findall(_split_compound(query).lower())
    return {w for w in words if w not in _GENERIC_QUERY_WORDS and not w.isdigit()}


def mentions_anchor(text: str, anchors: set[str]) -> bool:
    if not anchors:
        return False
    lowered = _split_compound(text).lower()
    tokens = set(_WORD.findall(lowered))
    squashed = re.sub(r"[^a-z0-9]", "", lowered)
    # whole-token match for short anchors ("pm", "pan"); substring for long ones ("myaadhaar")
    return any(a in tokens or (len(a) >= 5 and a in squashed) for a in anchors)


def foreign_entities(text: str, anchors: set[str]) -> set[str]:
    """Acronym-like entity names in text that are not the query's own anchors."""
    found = set()
    for token in _ACRONYM.findall(text):
        if token in _NON_ENTITY_UPPERCASE or token.lower() in anchors or token.isdigit():
            continue
        found.add(token.lower())
    return found


def entity_qualifier(clause_text: str, result_text: str, anchors: set[str]) -> str | None:
    """None = about the topic; otherwise an 'entity:...' qualifier (see rules above)."""
    if not anchors or mentions_anchor(clause_text, anchors):
        return None
    in_clause = foreign_entities(clause_text, anchors)
    if len(in_clause) == 1:
        return f"entity:{in_clause.pop()}"
    if in_clause or foreign_entities(result_text, anchors):
        return ENTITY_UNRESOLVED
    return None


def subject_key(qualifiers: list[str]) -> str:
    return "|".join(sorted(qualifiers))


# --- amount attributes -----------------------------------------------------------

_AMOUNT_CUES: list[tuple[str, re.Pattern]] = [
    ("late_fee", re.compile(r"\b(?:late\s+fees?|penalty|fine)\b", re.IGNORECASE)),
    ("fee", re.compile(r"\b(?:fees?|charges?|cost)\b", re.IGNORECASE)),
    ("annual_benefit", re.compile(r"\b(?:per\s+year|per\s+annum|annual(?:ly)?|yearly|a\s+year)\b", re.IGNORECASE)),
    ("installment_amount", re.compile(r"\b(?:(?:per|each)\s+instal{1,2}ments?|instal{1,2}ments?\s+of)\b", re.IGNORECASE)),
    # "₹2,000 every four months": a period describes the amount BEFORE it, so it only counts when it follows
    ("installment_period", re.compile(r"\bevery\s+(?:two|three|four|six|\d+)\s+months\b", re.IGNORECASE)),
    ("coverage", re.compile(r"\b(?:(?:health\s+)?cover(?:age)?|insurance)\b", re.IGNORECASE)),
]
_AMOUNT_PRIORITY = {name: i for i, (name, _) in enumerate(_AMOUNT_CUES)}
_FOLLOWING_ONLY = {"installment_period": "installment_amount"}
AMOUNT_CUE_MAX_DISTANCE = 50
AMOUNT_FOLLOWING_PENALTY = 15


def amount_attribute(text: str, clause: tuple[int, int], value_span: tuple[int, int]) -> str:
    """Nearest amount cue in the clause; longer (more specific) cues win when spans overlap."""
    segment = text[clause[0] : clause[1]]
    cues = [
        (name, clause[0] + m.start(), clause[0] + m.end())
        for name, pattern in _AMOUNT_CUES
        for m in pattern.finditer(segment)
    ]
    cues = [
        c for c in cues
        if not any(o is not c and o[1] <= c[1] and c[2] <= o[2] and (o[2] - o[1]) > (c[2] - c[1]) for o in cues)
        and not (c[1] < value_span[1] and value_span[0] < c[2])
    ]
    scored = []
    for name, s, e in cues:
        if name in _FOLLOWING_ONLY and s < value_span[1]:
            continue
        d = _gap((s, e), value_span) + (AMOUNT_FOLLOWING_PENALTY if s >= value_span[1] else 0)
        if d <= AMOUNT_CUE_MAX_DISTANCE:
            scored.append((d, _AMOUNT_PRIORITY[name], _FOLLOWING_ONLY.get(name, name)))
    return min(scored)[2] if scored else "amount"
