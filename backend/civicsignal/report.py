"""
Report generator: Investigation -> InvestigationReport (Evidence Map).

Answers, from the stored evidence only:
  * What was investigated, and what did the search landscape contain?
  * Which potential integrity issues were detected, and on what exact evidence?
  * Which claims conflict or repeat, who states each value, and how many
    apparently independent groups stand behind it?
  * Is there primary-source support, and where should the user verify?

Language rules: hedged descriptions only — never true/false/fake/correct.
URLs are passed through only when they are http(s).
"""

from __future__ import annotations

from collections import Counter
from urllib.parse import urlsplit

from .models import (
    ClaimEligibility,
    DataMode,
    Investigation,
    RelevanceLevel,
    SourceType,
    Strength,
)
from .models.report import (
    DataNotice,
    EvidenceItem,
    FollowUpView,
    InvestigationReport,
    IssueCard,
    LandscapeView,
    MapEntry,
    MapEvidence,
    MapSource,
    MapValue,
    StatusBlock,
    TrailStep,
    VerifySource,
)

STATUS_TEXT = {
    "CONFLICTING": ("Conflicting claims",
                    "Retrieved sources state different values for the same thing. See which sources state each value."),
    "POTENTIALLY_STALE": ("Potentially stale information",
                          "Some results present dates that have already passed as current or upcoming."),
    "INSUFFICIENT_EVIDENCE": ("Insufficient evidence",
                              "Most results do not address the question, or no search data was available."),
    "MINOR_ISSUES": ("Minor issues",
                     "Only lower-impact observations (source mix, relevance, repeated content) were made."),
    "NOT_ASSESSED": ("No issues detected by the checks run",
                     "None of the checks found an issue. This is not a verification of the information."),
    "MIXED_EVIDENCE": ("Mixed evidence", "The evidence points in different directions."),
    "CLEAR": ("No issues detected by the checks run",
              "None of the checks found an issue. This is not a verification of the information."),
}
CATEGORY = {
    "staleness": ("staleness", "Potentially stale"),
    "contradiction": ("conflicting_claims", "Conflicting claims"),
    "duplication": ("repeated_content", "Potentially repeated or syndicated content"),
    "authority": ("primary_source", "Primary-source support"),
    "relevance": ("relevance", "Search relevance"),
}
REASON_LABEL = {
    "primary_source_lookup": "Look for the official source",
    "recency_contrast": "Check pages from the past month",
    "news_check": "Check news coverage",
    "regional_compare": "Compare another language",
}
METHOD_NOTES = [
    "CivicSignal analyses the search results returned by SerpApi as evidence. It does not decide what is true.",
    "Findings describe potential information-integrity issues; each one links to the exact text it is based on.",
    "More sources repeating a value does not make it correct, and repeated values may not be independent.",
    "Official sources are context, not proof: an official page can also be outdated.",
    "Always confirm time-sensitive information with the official source before acting.",
]
OUTCOME_LABEL = {
    "support_found": "Official support found",
    "official_found_not_addressing_claims": "Official pages found, but they do not state the claim values",
    "no_useful_official_result": "No useful official result found",
    "no_results": "No results returned",
    "additional_relevant_evidence": "Additional relevant evidence found",
    "no_additional_relevant_evidence": "No additional relevant evidence found",
    "newer_relevant_evidence": "Newer relevant evidence found",
    "no_newer_relevant_evidence": "No newer relevant evidence found",
    "failed": "Follow-up search failed",
    "skipped": "Follow-up search skipped",
}
FOUND_BY_LABEL = {
    "base_query": "original search",
    "primary_source_lookup": "official-source lookup",
    "news_check": "news search",
    "recency_contrast": "past-month search",
    "regional_compare": "language comparison search",
}
ROLE_LABEL = {
    "deadline": "deadline",
    "expected_event": "expected date",
    "past_event": "past event",
    "historical_reference": "historical reference",
    "publication": "publication date",
    "updated": "update date",
    "eligibility_cutoff": "eligibility cutoff",
    "open_ended": "open-ended",
}
STATUS_LABEL = {"passed": "date has passed", "upcoming": "upcoming", "current": "current"}
SUPPORT_STATES = "An official source states this value"
SUPPORT_TOPIC_ONLY = "Official pages address the topic but do not state this value"
SUPPORT_NONE = "No official page addressing the topic was found"
EXCERPT_LIMIT = 220
_SEVERITY_ORDER = {Strength.HIGH: 0, Strength.MEDIUM: 1, Strength.LOW: 2}
_CATEGORY_ORDER = ["conflicting_claims", "staleness", "primary_source", "repeated_content", "relevance"]


def safe_url(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    return url.strip() if parts.scheme in ("http", "https") and parts.netloc else None


def _label(code: str) -> str:
    return code.replace("_", " ")


def excerpt(text: str, limit: int = EXCERPT_LIMIT) -> tuple[str, bool]:
    """
    UI excerpt: the evidence text itself if short enough, otherwise its longest prefix that ends
    on a word boundary within `limit`. Never rewritten — always an exact substring of `text`.
    Returns (excerpt, truncated).
    """
    if len(text) <= limit:
        return text, False
    cut = text.rfind(" ", 0, limit + 1)
    head = text[: cut if cut > 0 else limit].rstrip()
    return head, True


def value_label(value: str) -> str:
    """Display form of a normalized claim value. Formatting only; the value itself is unchanged."""
    from calendar import monthrange
    from datetime import date as _date

    def parse(text):
        try:
            return _date.fromisoformat(text)
        except ValueError:
            return None

    if "/" in value:
        start, _, end = value.partition("/")
        a, b = parse(start), parse(end)
        if a and b and a.day == 1 and b.day == monthrange(b.year, b.month)[1]:
            if (a.year, a.month) == (b.year, b.month):
                return a.strftime("%b %Y")
            return f"{a.strftime('%b')}–{b.strftime('%b %Y')}" if a.year == b.year else \
                f"{a.strftime('%b %Y')}–{b.strftime('%b %Y')}"
        if a and b:
            return f"{a.day} {a.strftime('%b %Y')} – {b.day} {b.strftime('%b %Y')}"
        return value
    d = parse(value)
    return f"{d.day} {d.strftime('%b %Y')}" if d else value


def _subject_label(subject: str) -> str:
    return subject.replace("|", ", ").replace(":", " ") if subject else "the investigated topic"


def _conflict_explanation(attribute: str, subject: str, values: list[MapValue]) -> str:
    """Neutral, fixed wording: lists the values in value order, says which an official source states."""
    listed = "; ".join(v.value_label or v.value for v in values)
    supported = [v.value_label or v.value for v in values if v.stated_by_official_source]
    unsupported = [v.value_label or v.value for v in values if not v.stated_by_official_source]
    if supported and unsupported:
        support = (f"An official source states {', '.join(supported)}; "
                   f"no retrieved official source states {', '.join(unsupported)}.")
    elif supported:
        support = "Official sources state each of these values."
    else:
        support = "No retrieved official source states any of these values."
    return (f"Relevant sources report different values for the {attribute} of {subject}: {listed}. {support} "
            "CivicSignal does not decide between them; check the official source before acting.")


def _recovery_sentence(inv: Investigation) -> str | None:
    """
    When the original search was mostly off-topic but the investigation did not end as
    INSUFFICIENT_EVIDENCE, say how the targeted searches recovered evidence. Counts only
    official pages that address the topic and were first found by a follow-up search.
    """
    base = inv.metrics.base_relevance_levels or {}
    if not base or not inv.follow_ups or inv.integrity_status.value == "INSUFFICIENT_EVIDENCE":
        return None
    if base.get("off_topic", 0) / sum(base.values()) < 0.5:
        return None
    recovered_official = {sid for f in inv.follow_ups for sid in f.official_relevant_source_ids
                          if sid in set(f.new_source_ids)}
    if recovered_official:
        return ("The original search did not address the question; targeted official-source searches recovered "
                f"{len(recovered_official)} relevant official page{'s' if len(recovered_official) != 1 else ''} "
                "that address the topic.")
    recovered = sum(f.relevant_results for f in inv.follow_ups)
    return ("The original search did not address the question; targeted follow-up searches recovered "
            f"{recovered} relevant result{'s' if recovered != 1 else ''}.")


def _unavailable_search(inv: Investigation) -> str | None:
    """Plain-language reason when the original search could not be run at all (else None)."""
    base = [r for r in inv.searches if r.request.reason.value == "base_query"]
    if not base or any(r.status.value != "failed" for r in base):
        return None
    error = base[0].error or ""
    if error.startswith("SerpApiFixtureNotFound"):
        return ("No captured SerpApi data exists for this question. In captured-data mode only the demo "
                "questions can be investigated — choose one of them, or run CivicSignal in live mode with a "
                "SerpApi API key.")
    if error.startswith("SerpApiConfigError"):
        return ("No SerpApi API key is configured, so no search could be run. Set SERPAPI_API_KEY, or start "
                "the backend in captured-data mode to use the demo questions (see the README).")
    return "The search could not be completed (" + error.split(": ", 1)[-1] + "). Please try again."


def _trail(inv: Investigation, follow_ups: list[FollowUpView], evidence_map: list[MapEntry],
           status_label: str) -> list[TrailStep]:
    """Audit trail of what the investigation did, built only from recorded facts."""
    steps: list[TrailStep] = []
    base = inv.metrics.base_relevance_levels or inv.metrics.relevance_levels or {}
    relevant = base.get("high", 0) + base.get("medium", 0)
    parts = [f"{relevant} relevant"]
    if base.get("low"):
        parts.append(f"{base['low']} weakly related")
    if base.get("off_topic"):
        parts.append(f"{base['off_topic']} off-topic")
    steps.append(TrailStep(kind="search",
                           text=f"Original search: {inv.metrics.base_results} results ({', '.join(parts)})."))
    for f in follow_ups:
        if f.triggered_by:
            shown = f.triggered_by[:2]
            more = f" (+{len(f.triggered_by) - 2} more)" if len(f.triggered_by) > 2 else ""
            steps.append(TrailStep(kind="gap", text="Gap detected: " + " ".join(shown) + more))
        steps.append(TrailStep(kind="action", text=f"Searched again — {f.reason_label}: \"{f.query}\"."))
        steps.append(TrailStep(kind="outcome", text=f"{f.outcome_label}. {f.outcome}"))
    if not follow_ups:
        steps.append(TrailStep(kind="result", text="No targeted follow-up search was needed."))
    conflicts = [m for m in evidence_map if m.has_conflict]
    for m in conflicts:
        steps.append(TrailStep(kind="result",
                               text=f"Conflict remains unresolved: sources report different values for the "
                                    f"{m.attribute} of {m.subject}."))
    steps.append(TrailStep(kind="result", text=f"Final status: {status_label}."))
    return steps


def build_report(inv: Investigation) -> InvestigationReport:
    results = {r.id: r for r in inv.results}
    sources = {s.id: s for s in inv.sources}
    spans = {e.id: e for e in inv.evidence_spans}
    runs = {s.id: s for s in inv.searches}

    def found_by(result_id: str) -> str:
        run = runs.get(results[result_id].search_run_id)
        return run.request.reason.value if run else "unknown"

    def evidence_for(finding) -> list[EvidenceItem]:
        items: list[EvidenceItem] = []
        seen = set()
        for sid in finding.evidence_span_ids:
            e = spans.get(sid)
            if e is None or (e.result_id, e.field, e.text) in seen:
                continue
            seen.add((e.result_id, e.field, e.text))
            r = results[e.result_id]
            src = sources.get(r.source_id) if r.source_id else None
            items.append(EvidenceItem(
                source_id=r.source_id, domain=r.domain, url=safe_url(r.link), title=r.title,
                source_type=src.source_type.value if src else None, search_position=r.position,
                found_by=found_by(r.id), field=e.field.value, quote=e.text, retrieved_at=r.retrieved_at,
            ))
        if not items:  # landscape findings (e.g. primary-source support): list the sources involved
            # no official source found: the evidence of absence is the set of sources that were examined
            ids = finding.source_ids or sorted(
                sources, key=lambda s: (sources[s].best_position is None, sources[s].best_position or 0)
            )[:10]
            for sid in ids:
                s = sources.get(sid)
                if s is None:
                    continue
                r = results[s.result_ids[0]]
                items.append(EvidenceItem(
                    source_id=s.id, domain=s.domain, url=safe_url(r.link), title=s.title or r.title,
                    source_type=s.source_type.value, search_position=s.best_position, found_by=found_by(r.id),
                    retrieved_at=r.retrieved_at,
                ))
        return items

    # --- issues ---------------------------------------------------------------------------
    issues = []
    for f in sorted(inv.findings, key=lambda f: _SEVERITY_ORDER[f.strength]):
        category, label = CATEGORY[f.kind]
        issues.append(IssueCard(
            finding_id=f.id, category=category, category_label=label, severity=f.strength.value,
            title=f.title, summary=f.explanation, evidence=evidence_for(f), how_to_verify=f.verification_hint,
        ))
    issues.sort(key=lambda c: (_CATEGORY_ORDER.index(c.category), _SEVERITY_ORDER[Strength(c.severity)]))

    # --- evidence map ----------------------------------------------------------------------
    claims = {c.id: c for c in inv.claims}
    official_ids_by_value = {(v.cluster_id, v.value): set(v.official_source_ids) for v in (inv.official_support or [])}
    groups_by_value = {}
    if inv.independence:
        groups_by_value = {(c.cluster_id, c.value): c.independent_groups for c in inv.independence.claim_support}
    stale_by_claim: dict[str, list[str]] = {}
    for f in inv.findings:
        if f.kind == "staleness":
            for cid in f.claim_ids:
                stale_by_claim.setdefault(cid, []).append(f.id)
    topic_official_exists = bool(inv.metrics.primary_sources)

    def map_source(sid: str) -> MapSource:
        s = sources[sid]
        return MapSource(source_id=sid, domain=s.domain, title=s.title, url=safe_url(s.urls[0] if s.urls else None),
                         source_type=s.source_type.value, official=s.source_type == SourceType.OFFICIAL)

    def value_evidence(claim_ids: list[str]) -> list[MapEvidence]:
        items, seen = [], set()
        for cid in claim_ids:
            c = claims.get(cid)
            if c is None:
                continue
            for eid in c.evidence_span_ids:
                e = spans.get(eid)
                if e is None or (e.result_id, e.field, e.text) in seen:
                    continue
                seen.add((e.result_id, e.field, e.text))
                r = results[e.result_id]
                text, cut = excerpt(e.text)
                role = found_by(r.id)
                items.append(MapEvidence(
                    claim_id=c.id, result_id=r.id, evidence_span_id=e.id, source_id=e.source_id,
                    search_run_id=r.search_run_id, domain=r.domain, field=e.field.value, excerpt=text,
                    truncated=cut, found_by=role, found_by_label=FOUND_BY_LABEL.get(role, _label(role)),
                ))
        return items

    evidence_map = []
    for cl in inv.clusters:
        values = []
        for g in cl.value_groups:
            group_claims = [claims[cid] for cid in g.claim_ids if cid in claims]
            official_ids = official_ids_by_value.get((cl.id, g.value), set())
            stale_ids = [fid for cid in g.claim_ids for fid in stale_by_claim.get(cid, [])]
            statuses = {STATUS_LABEL[c.temporal_status.value] for c in group_claims
                        if c.temporal_status.value in STATUS_LABEL}
            values.append(MapValue(
                value=g.value,
                value_label=value_label(g.value),
                sources=[map_source(sid) for sid in g.source_ids if sid in sources],
                independent_groups=groups_by_value.get((cl.id, g.value)),
                stated_by_official_source=bool(official_ids),
                value_type=cl.claim_type.value,
                temporal_roles=sorted({ROLE_LABEL[c.role.value] for c in group_claims
                                       if c.role.value in ROLE_LABEL}),
                temporal_status=(statuses.pop() if len(statuses) == 1 else "mixed") if statuses else None,
                potentially_stale=bool(stale_ids),
                stale_finding_ids=list(dict.fromkeys(stale_ids)),
                official_sources=[map_source(sid) for sid in g.source_ids if sid in sources and sid in official_ids],
                other_sources=[map_source(sid) for sid in g.source_ids
                               if sid in sources and sources[sid].source_type != SourceType.OFFICIAL],
                official_support_reason=(SUPPORT_STATES if official_ids
                                         else SUPPORT_TOPIC_ONLY if topic_official_exists else SUPPORT_NONE),
                evidence=value_evidence(g.claim_ids),
            ))
        subject, attribute = _subject_label(cl.subject), _label(cl.attribute)
        finding_ids = [f.id for f in inv.findings if f.kind == "contradiction" and f.cluster_id == cl.id]
        finding_ids += [fid for v in values for fid in v.stale_finding_ids]
        evidence_map.append(MapEntry(
            cluster_id=cl.id, subject=subject, attribute=attribute, has_conflict=cl.has_conflict, values=values,
            value_type=cl.claim_type.value,
            conflict_explanation=_conflict_explanation(attribute, subject, values) if cl.has_conflict else None,
            finding_ids=list(dict.fromkeys(finding_ids)),
        ))
    evidence_map.sort(key=lambda m: (not m.has_conflict, -sum(len(v.sources) for v in m.values)))

    # --- follow-ups, where to verify -------------------------------------------------------
    follow_ups = [
        FollowUpView(
            reason=f.reason.value, reason_label=REASON_LABEL.get(f.reason.value, _label(f.reason.value)),
            query=f.query, engine=f.engine, params=f.params, question=f.question, triggered_by=f.triggered_by,
            status=f.status.value, outcome_code=f.outcome_code,
            outcome_label=OUTCOME_LABEL.get(f.outcome_code, "Follow-up outcome"), results_added=f.results_added,
            relevant_results=f.relevant_results, new_sources=len(f.new_source_ids), outcome=f.summary,
        )
        for f in inv.follow_ups
    ]
    states_by_source: dict[str, list[str]] = {}
    for v in inv.official_support or []:
        for sid in v.official_source_ids:
            states_by_source.setdefault(sid, []).append(f"{_label(v.attribute)} {value_label(v.value)}")
    verify = []
    for s in inv.sources:
        if s.source_type != SourceType.OFFICIAL:
            continue
        rels = [results[rid] for rid in s.result_ids]
        relevant = [r for r in rels if r.relevance in (RelevanceLevel.HIGH, RelevanceLevel.MEDIUM)]
        if relevant:
            best = relevant[0]
            role_label = FOUND_BY_LABEL.get(found_by(best.id), _label(found_by(best.id)))
            verify.append(VerifySource(
                domain=s.domain, url=safe_url(rels[0].link), title=s.title or rels[0].title,
                states_values=states_by_source.get(s.id, []),
                why_relevant=(f"Official page that addresses the question ({best.relevance.value} relevance); "
                              f"found by the {role_label}."),
                found_by_label=role_label,
            ))
    verify_note = (
        "Official pages in the results that address this topic. Confirm the information there before acting."
        if verify else
        "No official page addressing this topic was found in the inspected results. "
        "Check the responsible department's official website directly."
    )

    # --- header ----------------------------------------------------------------------------
    label, explanation = STATUS_TEXT.get(inv.integrity_status.value, (_label(inv.integrity_status.value), ""))
    recovery = _recovery_sentence(inv)
    if recovery:
        explanation = f"{recovery} {explanation}"
    modes = set(inv.data_modes)
    unavailable = _unavailable_search(inv)
    if unavailable:
        explanation = unavailable
        notice = DataNotice(mode="unavailable", label="NO SEARCH DATA",
                            detail="No search results were retrieved for this question.")
    elif DataMode.MOCK in modes:
        captured = sorted({r.fetched_at.date().isoformat() for r in inv.searches if r.fetched_at})
        notice = DataNotice(mode="demo", label="CAPTURED SERPAPI DATA",
                            detail="Served from SerpApi responses captured on " + ", ".join(captured) + "; not a live search.")
    elif DataMode.CACHED in modes:
        notice = DataNotice(mode="cached", label="CACHED", detail="Some searches were served from a recent cache.")
    else:
        notice = DataNotice(mode="live", label="LIVE", detail="Searched live through SerpApi.")

    m = inv.metrics
    landscape = LandscapeView(
        result_appearances=m.total_search_results, unique_urls=m.unique_urls, unique_sources=m.unique_sources,
        unique_domains=m.unique_domains, independent_source_groups=m.independent_source_groups,
        claims_extracted=m.extracted_claims, claims_compared=sum(c.comparable for c in inv.claims),
        official_topic_sources=m.primary_sources, official_pages_found=m.official_sources,
        source_types=m.source_types or {}, relevance=m.relevance_levels or {},
        includes_follow_ups=m.follow_up_results > 0, base_results=m.base_results,
        base_relevance=m.base_relevance_levels or {}, follow_up_results=m.follow_up_results,
        follow_up_new_sources=m.follow_up_new_sources,
        follow_up_relevant_results=sum((m.follow_up_relevance_levels or {}).get(k, 0) for k in ("high", "medium")),
        follow_up_relevance=m.follow_up_relevance_levels or {},
    )
    excluded = Counter(c.eligibility.value for c in inv.claims if c.eligibility != ClaimEligibility.ELIGIBLE)

    trail = _trail(inv, follow_ups, evidence_map, label)

    return InvestigationReport(
        investigation_id=inv.id, question=inv.query, as_of_date=inv.as_of_date, created_at=inv.created_at,
        status=StatusBlock(code=inv.integrity_status.value, label=label, explanation=explanation),
        data_notice=notice, landscape=landscape,
        issue_counts=dict(Counter(c.category for c in issues)), issues=issues,
        evidence_map=evidence_map, excluded_claims=dict(excluded), follow_ups=follow_ups, trail=trail,
        where_to_verify=verify, where_to_verify_note=verify_note,
        checks_run={d.detector: d.status.value for d in inv.detector_runs},
        method_notes=METHOD_NOTES, warnings=inv.warnings,
    )
