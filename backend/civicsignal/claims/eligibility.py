"""
Relevance -> claim eligibility.

A claim taken from a result that does not address the investigated question
must not be able to create (or join) a conflict. Such claims are KEPT as
evidence and marked with why they are excluded:

  result relevance HIGH / MEDIUM -> unchanged (eligible if otherwise eligible)
  result relevance LOW           -> RESULT_WEAKLY_RELEVANT, not comparable
  result relevance OFF_TOPIC     -> RESULT_OFF_TOPIC, not comparable
  relevance not assessed         -> RELEVANCE_UNKNOWN, not comparable

Only claims that are currently ELIGIBLE are changed; other exclusion reasons
(non-comparable attribute, unresolved subject, no URL) are kept as they are.
"""

from __future__ import annotations

from ..models import ClaimEligibility, ExtractedClaim, RelevanceLevel, SearchResult

_EXCLUDED = {
    RelevanceLevel.LOW: (ClaimEligibility.RESULT_WEAKLY_RELEVANT, "its search result only weakly relates to the question"),
    RelevanceLevel.OFF_TOPIC: (ClaimEligibility.RESULT_OFF_TOPIC, "its search result does not address the question"),
}


def apply_relevance_eligibility(claims: list[ExtractedClaim], results: list[SearchResult]) -> int:
    """Downgrade eligible claims from weak/off-topic/unassessed results. Returns how many were excluded."""
    by_id = {r.id: r for r in results}
    excluded = 0
    for claim in claims:
        if claim.eligibility != ClaimEligibility.ELIGIBLE:
            continue
        level = by_id[claim.result_id].relevance
        if level is None:
            state, reason = ClaimEligibility.RELEVANCE_UNKNOWN, "relevance of its search result was not assessed"
        elif level in _EXCLUDED:
            state, reason = _EXCLUDED[level]
        else:
            continue
        claim.eligibility, claim.eligibility_reason, claim.comparable = state, reason, False
        excluded += 1
    return excluded
