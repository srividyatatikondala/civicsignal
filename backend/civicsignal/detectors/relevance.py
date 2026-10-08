"""
Relevance analyzer (lexical, deterministic).

Each search result is compared with the user's query using its title,
snippet and URL path:

  anchor coverage   share of the query's distinctive words (claims.subjects.query_anchors,
                    e.g. "aadhaar"; "pm", "kisan") mentioned by the result
  query-word match  share of all meaningful query words present (light plural stemming)
  year check        the query names a year (2026) and the result names years, none of them it

Levels (fixed rules):
  OFF_TOPIC  no anchor word at all (a 1-2 letter anchor such as "pm" only counts next to another anchor)
  LOW        fewer than half of the anchors (for a two-anchor topic: only one of them)
  MEDIUM     at least half of the anchors
  HIGH       all anchors and at least half of the query words
  A year mismatch lowers HIGH->MEDIUM and MEDIUM->LOW.
If the query has no distinctive words, query-word match alone is used.

Output: a level and human-readable signals on every result, plus ONE
landscape finding when results are weakly related or off-topic. This
describes the search results, not the topic itself. Lexical matching cannot
see homonyms ("Passport" the market-research product) — a known limitation.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urlsplit

from ..claims.subjects import mentions_anchor, query_anchors
from ..models import (
    EvidenceField,
    EvidenceSpan,
    FindingScope,
    RelevanceFinding,
    RelevanceLevel,
    SearchResult,
    Strength,
    new_id,
)

DETECTOR_NAME = "relevance"
_ORDER = [RelevanceLevel.HIGH, RelevanceLevel.MEDIUM, RelevanceLevel.LOW, RelevanceLevel.OFF_TOPIC]
_STOP = {
    "a", "an", "the", "of", "for", "in", "on", "to", "and", "or", "is", "are", "what", "when", "how",
    "which", "will", "be", "with", "by", "from", "my", "your", "i", "can", "do", "does",
}
_WORD = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"\b(20\d\d)\b")


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def _query_words(query: str) -> set[str]:
    return {_stem(w) for w in _WORD.findall(query.lower()) if w not in _STOP and not w.isdigit()}


SHORT_ANCHOR_LEN = 2  # "pm", "ug": too ambiguous alone ("12:04 PM")


def _anchor_present(anchor: str, anchors: set[str], text: str) -> bool:
    """
    A very short anchor counts only when it stands next to another anchor of the query
    ("PM Kisan", "PM-KISAN", "NEET UG"), not on its own (a clock time, an unrelated "UG").
    Single-anchor queries and longer anchors use the normal mention test.
    """
    if not mentions_anchor(text, {anchor}):
        return False
    others = anchors - {anchor}
    if len(anchor) > SHORT_ANCHOR_LEN or not others:
        return True
    tokens = _WORD.findall(re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text).lower())
    return any(
        t == anchor and any(tokens[j] in others for j in (i - 1, i + 1) if 0 <= j < len(tokens))
        for i, t in enumerate(tokens)
    )


def _result_text(r: SearchResult) -> str:
    path = ""
    if r.link:
        try:
            path = re.sub(r"[-_/+.]", " ", urlsplit(r.link).path)
        except ValueError:
            path = ""
    return f"{r.title} {r.snippet} {path}"


@dataclass
class RelevanceOutput:
    levels: dict[str, RelevanceLevel]  # result_id -> level
    findings: list[RelevanceFinding]
    evidence_spans: list[EvidenceSpan]

    def counts(self) -> dict[str, int]:
        return dict(Counter(l.value for l in self.levels.values()))


def assess(result: SearchResult, query: str) -> tuple[RelevanceLevel, list[str]]:
    text = _result_text(result)
    anchors = query_anchors(query)
    words = _query_words(query)
    tokens = {_stem(w) for w in _WORD.findall(text.lower())}
    matched_words = words & tokens
    word_share = len(matched_words) / len(words) if words else 0.0
    signals = [f"query words matched: {len(matched_words)}/{len(words)}"]

    if anchors:
        present = sorted(a for a in anchors if _anchor_present(a, anchors, text))
        missing = sorted(anchors - set(present))
        coverage = len(present) / len(anchors)
        if present:
            signals.append("mentions: " + ", ".join(present))
        if missing:
            signals.append("does not mention: " + ", ".join(missing))
        if coverage == 0:
            level = RelevanceLevel.OFF_TOPIC
        elif coverage < 0.5 or (len(anchors) == 2 and coverage < 1.0):
            # a two-word topic ("PM Kisan", "PAN Aadhaar") needs both words; one alone is a weak match
            level = RelevanceLevel.LOW
        elif coverage == 1.0 and word_share >= 0.5:
            level = RelevanceLevel.HIGH
        else:
            level = RelevanceLevel.MEDIUM
    else:
        level = (
            RelevanceLevel.HIGH if word_share >= 0.6
            else RelevanceLevel.MEDIUM if word_share >= 0.3
            else RelevanceLevel.LOW if word_share > 0
            else RelevanceLevel.OFF_TOPIC
        )

    query_years = set(_YEAR.findall(query))
    text_years = set(_YEAR.findall(text))
    if query_years and text_years and not (query_years & text_years):
        signals.append(f"mentions {', '.join(sorted(text_years))} but not {', '.join(sorted(query_years))}")
        if level in (RelevanceLevel.HIGH, RelevanceLevel.MEDIUM):
            level = _ORDER[_ORDER.index(level) + 1]
    return level, signals


def analyze_relevance(
    results: list[SearchResult], query: str, base_run_ids: set[str] | None = None
) -> RelevanceOutput:
    """
    Rates EVERY result (the per-result level gates claim eligibility for follow-up evidence too),
    but the landscape finding describes the ORIGINAL search only when `base_run_ids` is given:
    targeted follow-up searches answer a different question and must not redefine how
    relevant the user's original search was.
    """
    levels: dict[str, RelevanceLevel] = {}
    for r in results:
        level, signals = assess(r, query)
        r.relevance, r.relevance_signals = level, signals
        levels[r.id] = level

    scoped = [r for r in results if base_run_ids is None or r.search_run_id in base_run_ids]
    has_follow_ups = len(scoped) < len(results)
    weak = [r for r in scoped if levels[r.id] in (RelevanceLevel.LOW, RelevanceLevel.OFF_TOPIC)]
    findings: list[RelevanceFinding] = []
    spans: list[EvidenceSpan] = []
    for r in weak:  # the title/snippet that was judged is the evidence
        for field, value in ((EvidenceField.TITLE, r.title), (EvidenceField.SNIPPET, r.snippet)):
            if value:
                spans.append(EvidenceSpan(id=new_id("ev"), result_id=r.id, source_id=r.source_id, field=field,
                                          text=value, start=0, end=len(value)))
    if weak and scoped:
        share = len(weak) / len(scoped)
        off = [r for r in weak if levels[r.id] == RelevanceLevel.OFF_TOPIC]
        anchors = sorted(query_anchors(query))
        about = f" ({', '.join(anchors)})" if anchors else ""
        counts = dict(Counter(levels[r.id].value for r in scoped))
        where = "results from the original search" if has_follow_ups else "retrieved results"
        findings.append(
            RelevanceFinding(
                id=new_id("fnd"),
                detector=DETECTOR_NAME,
                title="Search results include weakly related or off-topic material",
                explanation=(
                    f"{len(weak)} of {len(scoped)} {where} do not clearly address the question"
                    f"{about}: {len(off)} mention none of its key terms and {len(weak) - len(off)} only some. "
                    "This describes what the search returned, not the topic itself; answers may be missing "
                    "from these results."
                ),
                strength=Strength.HIGH if share >= 0.5 else Strength.MEDIUM if share >= 0.25 else Strength.LOW,
                scope=FindingScope.LANDSCAPE,
                source_ids=list(dict.fromkeys(r.source_id for r in weak if r.source_id)),
                result_ids=[r.id for r in weak],
                evidence_span_ids=[e.id for e in spans],
                verification_hint="Rephrase the query or search the official source directly.",
                level="off_topic" if off else "low",
                level_counts=counts,
                total_results=len(scoped),
            )
        )
    return RelevanceOutput(levels=levels, findings=findings, evidence_spans=spans)
