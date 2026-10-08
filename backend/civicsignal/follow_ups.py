"""
Follow-up outcome summaries.

For every follow-up search, report what it contributed — in evidence terms —
keeping three official-source concepts strictly apart:

  official page found               any official domain was returned
  official page addressing topic    official AND rated high/medium relevance
  official support for a value      an official page addressing the topic STATES that claim value

Outcome codes
  PRIMARY_SOURCE_LOOKUP  support_found | official_found_not_addressing_claims |
                         no_useful_official_result | no_results | failed | skipped
  NEWS_CHECK, REGIONAL_COMPARE
                         additional_relevant_evidence | no_additional_relevant_evidence |
                         no_results | failed | skipped
  RECENCY_CONTRAST       newer_relevant_evidence | no_newer_relevant_evidence |
                         no_results | failed | skipped

Never "true", "false", "correct" or "incorrect": an official page stating a
value is support, not proof; an official page not mentioning a value is "not
addressed", not a contradiction.
"""

from __future__ import annotations

from .models import (
    FollowUpOutcome,
    Investigation,
    RelevanceLevel,
    SearchReason,
    SearchRunStatus,
    SourceType,
)

_RELEVANT = (RelevanceLevel.HIGH, RelevanceLevel.MEDIUM)
_NOT_EXECUTED = {SearchRunStatus.FAILED: "failed", SearchRunStatus.SKIPPED: "skipped"}


def summarize_follow_ups(inv: Investigation) -> list[FollowUpOutcome]:
    results_by_run: dict[str, list] = {}
    for r in inv.results:
        results_by_run.setdefault(r.search_run_id, []).append(r)
    sources = {s.id: s for s in inv.sources}
    first_seen: dict[str, str] = {}
    for run in inv.searches:  # in search order
        for r in results_by_run.get(run.id, []):
            if r.source_id and r.source_id not in first_seen:
                first_seen[r.source_id] = run.id

    outcomes = []
    for run in inv.searches:
        reason = run.request.reason
        if reason == SearchReason.BASE_QUERY:
            continue
        found = results_by_run.get(run.id, [])
        relevant = [r for r in found if r.relevance in _RELEVANT]
        new_sources = [s for s, rid in first_seen.items() if rid == run.id]
        official = list(dict.fromkeys(
            r.source_id for r in relevant
            if r.source_id and sources[r.source_id].source_type == SourceType.OFFICIAL
        ))
        supported, not_addressed = [], []
        for v in inv.official_support or []:
            label = f"{v.attribute.replace('_', ' ')} {v.value}"
            (supported if set(v.official_source_ids) & set(official) else not_addressed).append(label)

        executed = run.status in (SearchRunStatus.OK, SearchRunStatus.EMPTY)
        is_primary = reason == SearchReason.PRIMARY_SOURCE_LOOKUP
        domains = sorted({sources[s].domain for s in official})

        if not executed:
            code = _NOT_EXECUTED.get(run.status, run.status.value)
            summary = ("The follow-up search failed; no evidence was added." if code == "failed"
                       else "Planned but not executed (no captured data for it in demo mode).")
        elif not found:
            code, summary = "no_results", "The search returned no results."
        elif is_primary:
            if supported:
                code = "support_found"
                summary = (f"Official source support found ({', '.join(domains)}). "
                           "Values stated by an official source: " + "; ".join(supported) + ".")
                if not_addressed:
                    summary += " Not addressed by the official source(s): " + "; ".join(not_addressed) + "."
            elif official:
                code = "official_found_not_addressing_claims"
                summary = f"Official pages addressing the topic were found ({', '.join(domains)}), "
                summary += ("but they do not state any of the claim values in the results: "
                            + "; ".join(not_addressed) + "." if not_addressed
                            else "but there were no comparable claim values to check against them.")
            else:
                code = "no_useful_official_result"
                summary = "No official page addressing the topic was returned."
        else:
            newer = reason == SearchReason.RECENCY_CONTRAST
            if relevant:
                code = "newer_relevant_evidence" if newer else "additional_relevant_evidence"
                summary = (f"{len(relevant)} relevant result(s) {'from the past month ' if newer else ''}"
                           f"added; {len(new_sources)} new page(s) entered the evidence.")
            else:
                code = "no_newer_relevant_evidence" if newer else "no_additional_relevant_evidence"
                summary = (f"{len(found)} result(s) returned, none of them relevant to the question; "
                           "nothing was added to the comparison.")

        outcomes.append(
            FollowUpOutcome(
                search_run_id=run.id,
                reason=reason,
                query=run.request.q,
                engine=run.request.engine,
                params=dict(run.request.params),
                question=run.question,
                triggered_by=run.triggered_by,
                status=run.status,
                outcome_code=code,
                results_added=len(found),
                relevant_results=len(relevant),
                new_source_ids=new_sources,
                official_relevant_source_ids=official,
                official_topic_pages_found=(bool(official) if executed else None) if is_primary else None,
                primary_source_support_found=(bool(supported) if executed else None) if is_primary else None,
                values_with_official_support=supported if executed else [],
                values_not_addressed_by_official_sources=not_addressed if executed else [],
                summary=summary,
            )
        )
    return outcomes
