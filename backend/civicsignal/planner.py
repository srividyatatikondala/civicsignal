"""
Follow-up search planner (deterministic, evidence-triggered).

After the base search has been analysed, the planner decides whether a
targeted follow-up search is NECESSARY. It never searches because it can:
every planned search records why it was needed, which evidence triggered it,
and which question it tries to resolve. It finds evidence; it never decides
what is true.

Rules, in priority order (budget: settings.max_follow_up_searches, also capped
by max_searches_per_investigation):

  PRIMARY_SOURCE_LOOKUP
    trigger  (a) no official source that addresses the topic, or
             (b) a conflict exists and none of its values is stated by an official source
    query    the user's question, sharpened by the conflict's qualifier
             ("next installment" -> "23rd installment"), restricted to:
               (b) the relevant official domains already found, else
               official domains MENTIONED inside relevant result text
                   ("details on https://scheme.gov.in"), else
               generic official suffixes (site:gov.in OR site:nic.in)
  REGIONAL_COMPARE
    trigger  only when the user explicitly asks (compare_hl)
  NEWS_CHECK  (engine google_news)
    trigger  an "expected" date has already passed: has the event since been reported?
  RECENCY_CONTRAST  (tbs=qdr:m, past month)
    trigger  a stated deadline has already passed: do recent pages say something else?

Nothing here refers to a particular scheme, site or query.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from .models import (
    Investigation,
    RelevanceLevel,
    SearchReason,
    SearchRequest,
    SourceType,
    TemporalRole,
)

_OFFICIAL_MENTION = re.compile(r"\b((?:[a-z0-9-]+\.)+(?:gov|nic)\.in)\b", re.IGNORECASE)
_GENERIC_OFFICIAL_FILTER = "(site:gov.in OR site:nic.in)"
_DROP_WORDS = {"next", "latest", "upcoming", "new"}
_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}
_ATTRIBUTE_WORDS = {"deadline": "last date", "expected_date": "date", "fee": "fee", "late_fee": "late fee"}
_RELEVANT = (RelevanceLevel.HIGH, RelevanceLevel.MEDIUM)


@dataclass
class PlannedSearch:
    request: SearchRequest
    question: str
    triggered_by: list[str] = field(default_factory=list)
    parent_search_id: str | None = None


def _ordinal(n: int) -> str:
    suffix = "th" if 10 <= n % 100 <= 20 else _ORDINAL_SUFFIX.get(n % 10, "th")
    return f"{n}{suffix}"


def _sharpen(query: str, subject: str) -> str:
    """Insert an installment/round ordinal from the claim subject into the user's question."""
    qualifier = next((q for q in subject.split("|") if q.split(":")[0] in ("installment", "round")), None)
    if not qualifier:
        return query
    unit, number = qualifier.split(":")
    if not number.isdigit():
        return query
    phrase = _ordinal(int(number))
    words = [w for w in query.split() if w.lower() not in _DROP_WORDS]
    for i, w in enumerate(words):
        if w.lower().startswith(unit[:7]):  # installment/instalment, round
            return " ".join(words[:i] + [phrase] + words[i:])
    return " ".join(words + [phrase, unit])


def _official_relevant_sources(inv: Investigation):
    results = {r.id: r for r in inv.results}
    return [
        s for s in inv.sources
        if s.source_type == SourceType.OFFICIAL and any(results[rid].relevance in _RELEVANT for rid in s.result_ids)
    ]


def _mentioned_official_domains(inv: Investigation) -> list[tuple[str, str]]:
    """(domain, where) for official domains named inside relevant results' text but not their own host."""
    found: list[tuple[str, str]] = []
    for r in inv.results:
        if r.relevance not in _RELEVANT:
            continue
        for m in _OFFICIAL_MENTION.finditer(f"{r.title} {r.snippet}"):
            domain = m.group(1).lower().removeprefix("www.")
            if domain != (r.domain or ""):
                found.append((domain, f"result #{r.position} ({r.domain})"))
    return found


def _unbacked_conflicts(inv: Investigation):
    """Contradiction findings whose cluster has no value stated by an official source."""
    support = inv.official_support or []
    backed_clusters = {v.cluster_id for v in support if v.official_source_ids}
    return [f for f in inv.findings if f.kind == "contradiction" and f.cluster_id not in backed_clusters]


def _base_params(inv: Investigation) -> dict:
    base = next((s for s in inv.searches if s.request.reason == SearchReason.BASE_QUERY), None)
    return dict(base.request.params) if base else {}


def plan_follow_ups(inv: Investigation, budget: int, compare_hl: str | None = None) -> list[PlannedSearch]:
    if budget <= 0 or not inv.results:
        return []
    base = next((s for s in inv.searches if s.request.reason == SearchReason.BASE_QUERY), None)
    parent = base.id if base else None
    params = _base_params(inv)
    plans: list[PlannedSearch] = []

    # --- PRIMARY_SOURCE_LOOKUP -----------------------------------------------------
    primary = inv.metrics.primary_sources
    official_relevant = _official_relevant_sources(inv)
    unbacked = _unbacked_conflicts(inv)
    if primary is not None and (primary == 0 or unbacked):
        evidence: list[str] = []
        focus = None
        if unbacked:
            focus = max(unbacked, key=lambda f: len(f.source_ids))
            evidence.append(
                f"Sources disagree on the {focus.attribute.replace('_', ' ')}"
                f"{' for ' + focus.subject.replace(':', ' ') if focus.subject else ''}: "
                + " vs ".join(focus.conflicting_values)
                + " — none of these values is stated by an official source in the results."
            )
        if primary == 0:
            evidence.append(f"No official page that addresses the topic among {len(inv.sources)} retrieved sources.")
        if primary and official_relevant:  # official pages exist but do not settle the conflict
            domains = sorted({s.domain for s in official_relevant})[:2]
            evidence.append("Relevant official source(s) already present: " + ", ".join(domains) + ".")
        else:
            mentions = _mentioned_official_domains(inv)
            counted = Counter(d for d, _ in mentions)
            domains = [d for d, _ in counted.most_common(2)]
            for d in domains:
                where = next(w for dd, w in mentions if dd == d)
                evidence.append(f"Official domain {d} is mentioned in {where}.")
        site_filter = " OR ".join(f"site:{d}" for d in domains) if domains else _GENERIC_OFFICIAL_FILTER
        if len(domains) > 1:
            site_filter = f"({site_filter})"
        topic = _sharpen(inv.query, focus.subject) if focus else inv.query
        attr = _ATTRIBUTE_WORDS.get(focus.attribute, "") if focus else ""
        question = (
            f"Does an official source state the {attr or 'information'} for this topic, "
            + (f"and which of {', '.join(focus.conflicting_values)} does it support?" if focus else "and what does it say?")
        )
        plans.append(
            PlannedSearch(
                request=SearchRequest(
                    engine="google", q=f"{topic} {site_filter}", params=params,
                    reason=SearchReason.PRIMARY_SOURCE_LOOKUP,
                    reason_detail="Locate the official/primary source for the investigated claim(s).",
                ),
                question=question, triggered_by=evidence, parent_search_id=parent,
            )
        )

    # --- REGIONAL_COMPARE (explicit request only) --------------------------------------
    if compare_hl and compare_hl != params.get("hl"):
        plans.append(
            PlannedSearch(
                request=SearchRequest(
                    engine="google", q=inv.query, params={**params, "hl": compare_hl},
                    reason=SearchReason.REGIONAL_COMPARE,
                    reason_detail=f"User requested a comparison with the '{compare_hl}' result landscape.",
                ),
                question=f"Does the search landscape in '{compare_hl}' contain different sources or claims?",
                triggered_by=[f"Explicit user request (compare_hl={compare_hl})."], parent_search_id=parent,
            )
        )

    stale = [f for f in inv.findings if f.kind == "staleness"]
    results = {r.id: r for r in inv.results}

    def describe(f) -> str:
        r = results[f.result_ids[0]]
        return f"{r.domain} (#{r.position}) gives {f.date_end}, {f.days_expired} day(s) before {f.as_of_date}"

    # --- NEWS_CHECK ------------------------------------------------------------------
    expected = [f for f in stale if f.temporal_role == TemporalRole.EXPECTED_EVENT]
    if expected:
        plans.append(
            PlannedSearch(
                request=SearchRequest(
                    engine="google_news", q=inv.query, params={k: v for k, v in params.items() if k != "num"},
                    reason=SearchReason.NEWS_CHECK,
                    reason_detail="Expected dates in the results have already passed.",
                ),
                question="Has the expected event since been reported as announced, released or rescheduled?",
                triggered_by=["Expected date already passed: " + describe(f) for f in expected[:3]],
                parent_search_id=parent,
            )
        )

    # --- RECENCY_CONTRAST --------------------------------------------------------------
    deadlines = [f for f in stale if f.temporal_role == TemporalRole.DEADLINE]
    if deadlines:
        plans.append(
            PlannedSearch(
                request=SearchRequest(
                    engine="google", q=inv.query, params={**params, "tbs": "qdr:m"},
                    reason=SearchReason.RECENCY_CONTRAST,
                    reason_detail="Stated deadlines have already passed; compare with pages from the past month.",
                ),
                question="Do pages published in the past month state a different (current) deadline?",
                triggered_by=["Deadline already passed: " + describe(f) for f in deadlines[:3]],
                parent_search_id=parent,
            )
        )

    done = {s.request.cache_key() for s in inv.searches}
    unique = [p for p in plans if p.request.cache_key() not in done]
    return unique[:budget]
