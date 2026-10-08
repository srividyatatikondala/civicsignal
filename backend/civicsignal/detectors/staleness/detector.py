"""
Staleness detector.

Works on typed date claims (claims/extract.py): each date mention in a
result's title or snippet carries a temporal role from its nearest cue in
the same clause (roles.py). A finding is raised when:

  * role = deadline       and the whole claimed period has ended before as_of_date
  * role = expected_event and the whole claimed period has ended before as_of_date
    (an "expected" date that has already gone by is presented as upcoming)

Never flagged: historical references, open-ended/rolling, publication or
update stamps, past events, unknown roles, and periods that have not ended.
Also not flagged: claims from procurement notices (bid dates) and from
results rated OFF_TOPIC — a date on a page that does not address the
question is not stale information about it. Weakly related results are
still checked.

Severity (configurable, see StalenessConfig):
  HIGH   expired > high_days AND the result is ranked at position <= high_max_position
  MEDIUM expired > medium_days
  LOW    otherwise
Non-ranked results (answer box, "People also ask") never reach HIGH.

Counting: one claim per distinct (subject, attribute, value) per result — a
date repeated in the title and snippet is one claim with two evidence spans.
A finding is a *detection event* on one result; several findings may point
to the same source. Callers must count sources and domains separately.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ...models import (
    ClaimEligibility,
    ClaimType,
    RelevanceLevel,
    EvidenceField,
    EvidenceSpan,
    ExtractedClaim,
    FindingScope,
    ResultType,
    SearchResult,
    StalenessFinding,
    Strength,
    TemporalRole,
    new_id,
)
from .dates import extract_date_claims
from .roles import classify_date_role

DETECTOR_NAME = "staleness"
_RANKED_TYPES = {ResultType.ORGANIC, ResultType.NEWS}
_STALE_ROLES = {TemporalRole.DEADLINE, TemporalRole.EXPECTED_EVENT}


@dataclass(frozen=True)
class StalenessConfig:
    high_days: int = 60
    high_max_position: int = 5
    medium_days: int = 14


@dataclass
class DateMention:
    """One date found in one text field, with its classified role."""

    field: EvidenceField
    text: str  # the full field text
    date_text: str
    date_start: date
    date_end: date
    precision: str
    ambiguous: bool
    role: TemporalRole
    cue_text: str | None
    clause_start: int
    clause_end: int

    @property
    def clause(self) -> str:
        return self.text[self.clause_start : self.clause_end].strip()


def analyze_text(text: str, field: EvidenceField = EvidenceField.SNIPPET) -> list[DateMention]:
    """Extract and classify every date mention in one piece of text."""
    if not text:
        return []
    dates = extract_date_claims(text)
    protected = [(d.start_idx, d.end_idx) for d in dates]
    mentions = []
    for d in dates:
        decision = classify_date_role(text, d.start_idx, d.end_idx, protected)
        mentions.append(
            DateMention(
                field=field,
                text=text,
                date_text=d.matched_text,
                date_start=d.date_start,
                date_end=d.date_end,
                precision=d.precision,
                ambiguous=d.ambiguous,
                role=decision.role,
                cue_text=decision.cue.text if decision.cue else None,
                clause_start=decision.clause_start,
                clause_end=decision.clause_end,
            )
        )
    return mentions


def severity(days_expired: int, position: int | None, config: StalenessConfig) -> Strength:
    if days_expired > config.high_days and position is not None and position <= config.high_max_position:
        return Strength.HIGH
    if days_expired > config.medium_days:
        return Strength.MEDIUM
    return Strength.LOW


@dataclass
class StalenessOutput:
    evidence_spans: list[EvidenceSpan] = field(default_factory=list)
    claims: list[ExtractedClaim] = field(default_factory=list)
    findings: list[StalenessFinding] = field(default_factory=list)
    role_counts: dict[str, int] = field(default_factory=dict)

    def summary(self) -> str:
        sources = {sid for f in self.findings for sid in f.source_ids}
        roles = ", ".join(f"{k}={v}" for k, v in sorted(self.role_counts.items()))
        return (
            f"{len(self.claims)} date claim(s) [{roles or 'none'}]; "
            f"{len(self.findings)} potentially stale finding(s) on {len(sources)} source(s)."
        )


def _finding_text(claim: ExtractedClaim, days: int, as_of: date) -> tuple[str, str]:
    d = claim.date
    assert d is not None
    period = d.matched_text if d.precision.value == "day" else f"{d.matched_text} (period ended {d.date_end.isoformat()})"
    if claim.role == TemporalRole.EXPECTED_EVENT:
        title = "Expected date has already passed"
        what = f"presents {period} as an expected/upcoming date"
    else:
        title = "Potentially stale deadline"
        what = f"states a deadline of {period}"
    explanation = (
        f"This result {what}. As of {as_of.isoformat()}, that date passed {days} day(s) ago, "
        "so the information may be outdated."
    )
    if d.ambiguous:
        explanation += " Note: the numeric date is ambiguous (DD/MM vs MM/DD); it was read as DD/MM."
    return title, explanation


def find_stale(
    claims: list[ExtractedClaim],
    results: list[SearchResult],
    as_of: date,
    config: StalenessConfig | None = None,
) -> list[StalenessFinding]:
    """Staleness findings for date claims whose deadline/expected period has fully passed."""
    config = config or StalenessConfig()
    by_id = {r.id: r for r in results}
    findings = []
    for claim in claims:
        if claim.claim_type != ClaimType.DATE or claim.date is None or claim.role not in _STALE_ROLES:
            continue
        if claim.date.date_end >= as_of:
            continue
        result = by_id[claim.result_id]
        if claim.eligibility == ClaimEligibility.PROCUREMENT_NOTICE or result.relevance == RelevanceLevel.OFF_TOPIC:
            continue
        position = result.position if result.result_type in _RANKED_TYPES else None
        days = (as_of - claim.date.date_end).days
        title, explanation = _finding_text(claim, days, as_of)
        findings.append(
            StalenessFinding(
                id=new_id("fnd"),
                detector=DETECTOR_NAME,
                title=title,
                explanation=explanation,
                strength=severity(days, position, config),
                scope=FindingScope.WITHIN_SOURCE,
                source_ids=[claim.source_id] if claim.source_id else [],
                result_ids=[claim.result_id],
                claim_ids=[claim.id],
                evidence_span_ids=claim.evidence_span_ids,
                verification_hint="Check the official/primary source for the current date before acting.",
                as_of_date=as_of.isoformat(),
                temporal_role=claim.role,
                date_start=claim.date.date_start.isoformat(),
                date_end=claim.date.date_end.isoformat(),
                days_expired=days,
            )
        )
    order = {Strength.HIGH: 0, Strength.MEDIUM: 1, Strength.LOW: 2}
    findings.sort(key=lambda f: (order[f.strength], -(f.days_expired or 0)))
    return findings


def run_staleness(
    results: list[SearchResult], as_of: date, config: StalenessConfig | None = None
) -> StalenessOutput:
    """Convenience wrapper: extract typed claims from results, then find stale date claims."""
    from ...claims.extract import extract_claims  # local import: claims depends on this package

    extraction = extract_claims(results, as_of)
    date_claims = [c for c in extraction.claims if c.claim_type == ClaimType.DATE]
    used_spans = {sid for c in date_claims for sid in c.evidence_span_ids}
    out = StalenessOutput(
        evidence_spans=[e for e in extraction.evidence_spans if e.id in used_spans],
        claims=date_claims,
        findings=find_stale(date_claims, results, as_of, config),
    )
    for c in date_claims:
        out.role_counts[c.role.value] = out.role_counts.get(c.role.value, 0) + 1
    return out
