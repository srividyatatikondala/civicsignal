"""
Deterministic typed-claim extraction.

For every search result, the title and snippet are scanned separately for:
  * dates   -> role from the nearest temporal cue (detectors/staleness/roles.py)
  * amounts -> attribute from the nearest amount cue (subjects.amount_attribute)

Each claim records subject qualifiers, attribute, normalized value, value
type, temporal role/status, source, and the exact evidence span(s). Within
one result, the same (type, subject, attribute, value) found in both title
and snippet is ONE claim with two evidence spans.

Only these attributes are compared for conflicts (comparable=True):
  dates:   deadline, expected_date
  amounts: fee, late_fee, annual_benefit, installment_amount, coverage
Historical, past-event, publication, eligibility, open-ended and unknown
dates, aggregate (crore/lakh) amounts, and amounts with no attribute cue are
extracted but never compared.

Subjects are anchored to the investigation topic only when the text
justifies it (subjects.entity_qualifier): a clause that names a different
programme/organisation gets that entity as its subject, and a clause that
names nothing on a page that also covers other entities is "unresolved"
and never compared.

Procurement notices (tenders, RFPs, bid submissions, corrigenda) are a
different kind of document: their dates are bid/opening dates, not claims
about the investigated topic. Their claims are kept as evidence but marked
PROCUREMENT_NOTICE and never compared or checked for staleness.

Two general exclusions:
  * Questions are not claims: values inside a clause ending in "?" (e.g.
    "People also ask" questions) are skipped.
  * Installment/round numbers qualify only installment-scoped attributes.
    Fees, late fees, annual benefits and coverage are properties of the
    scheme, so an ordinal nearby (or in the title) does not narrow them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from ..detectors.staleness.dates import extract_date_claims
from ..detectors.staleness.roles import classify_date_role, clause_bounds
from ..models import (
    AmountEvidence,
    ClaimEligibility,
    ClaimType,
    DateEvidence,
    DatePrecision,
    EvidenceField,
    EvidenceSpan,
    ExtractedClaim,
    SearchResult,
    TemporalRole,
    TemporalStatus,
    new_id,
)
from .amounts import extract_amounts, format_inr
from .subjects import (
    amount_attribute,
    entity_qualifier,
    is_ambiguous,
    query_anchors,
    subject_key,
    subject_qualifiers,
)

DATE_ATTRIBUTES = {
    TemporalRole.DEADLINE: "deadline",
    TemporalRole.EXPECTED_EVENT: "expected_date",
    TemporalRole.PAST_EVENT: "event_date",
    TemporalRole.HISTORICAL_REFERENCE: "historical_date",
    TemporalRole.PUBLICATION: "publication_date",
    TemporalRole.UPDATED: "publication_date",
    TemporalRole.ELIGIBILITY_CUTOFF: "eligibility_cutoff",
    TemporalRole.OPEN_ENDED: "open_ended_date",
    TemporalRole.UNKNOWN: "unspecified_date",
}
SCHEME_LEVEL_ATTRIBUTES = {"fee", "late_fee", "annual_benefit", "coverage"}
COMPARABLE_ATTRIBUTES = {
    "deadline", "expected_date",
    "fee", "late_fee", "annual_benefit", "installment_amount", "coverage",
}


@dataclass
class _Found:
    """One value found in one field, before per-result deduplication."""

    field: EvidenceField
    text: str
    clause: tuple[int, int]
    claim_type: ClaimType
    qualifiers: list[str]
    attribute: str
    value: str
    role: TemporalRole = TemporalRole.UNKNOWN
    date: DateEvidence | None = None
    amount: AmountEvidence | None = None


@dataclass
class ClaimExtraction:
    evidence_spans: list[EvidenceSpan] = field(default_factory=list)
    claims: list[ExtractedClaim] = field(default_factory=list)


def _trimmed(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


def _date_value(start: date, end: date) -> str:
    return end.isoformat() if start == end else f"{start.isoformat()}/{end.isoformat()}"


def _temporal_status(start: date, end: date, as_of: date) -> TemporalStatus:
    if end < as_of:
        return TemporalStatus.PASSED
    if start > as_of:
        return TemporalStatus.UPCOMING
    return TemporalStatus.CURRENT


def _scan_field(
    text: str, field_name: EvidenceField, title: str, anchors: set[str], result_text: str
) -> list[_Found]:
    if not text:
        return []
    dates = extract_date_claims(text)
    amounts = extract_amounts(text)
    date_spans = [(d.start_idx, d.end_idx) for d in dates]
    all_spans = date_spans + [(a.start, a.end) for a in amounts]
    clauses = clause_bounds(text, all_spans)

    def clause_of(pos: int) -> tuple[int, int]:
        return next(((a, b) for a, b in clauses if a <= pos < b), (0, len(text)))

    def is_question(clause: tuple[int, int]) -> bool:
        return text[clause[0] : clause[1]].strip().endswith("?")

    def with_entity(qualifiers: list[str], clause: tuple[int, int]) -> list[str]:
        entity = entity_qualifier(text[clause[0] : clause[1]], result_text, anchors)
        return sorted(qualifiers + [entity]) if entity else qualifiers

    found: list[_Found] = []
    for d in dates:
        decision = classify_date_role(text, d.start_idx, d.end_idx, date_spans)
        clause = (decision.clause_start, decision.clause_end)
        if is_question(clause):
            continue
        found.append(
            _Found(
                field=field_name,
                text=text,
                clause=clause,
                claim_type=ClaimType.DATE,
                qualifiers=with_entity(subject_qualifiers(text, clause, (d.start_idx, d.end_idx), title), clause),
                attribute=DATE_ATTRIBUTES[decision.role],
                value=_date_value(d.date_start, d.date_end),
                role=decision.role,
                date=DateEvidence(
                    matched_text=d.matched_text,
                    precision=DatePrecision(d.precision),
                    date_start=d.date_start,
                    date_end=d.date_end,
                    ambiguous=d.ambiguous,
                ),
            )
        )
    for a in amounts:
        clause = clause_of(a.start)
        if is_question(clause):
            continue
        attribute = "aggregate_amount" if a.aggregate else amount_attribute(text, clause, (a.start, a.end))
        value = format_inr(a.value) if a.qualifier is None else f"{a.qualifier} {format_inr(a.value)}"
        qualifiers = subject_qualifiers(text, clause, (a.start, a.end), title)
        if attribute in SCHEME_LEVEL_ATTRIBUTES:
            qualifiers = [q for q in qualifiers if q.split(":")[0] not in ("installment", "round")]
        qualifiers = with_entity(qualifiers, clause)
        found.append(
            _Found(
                field=field_name,
                text=text,
                clause=clause,
                claim_type=ClaimType.AMOUNT,
                qualifiers=qualifiers,
                attribute=attribute,
                value=value,
                amount=AmountEvidence(
                    matched_text=a.matched_text,
                    value=a.value,
                    low=a.low,
                    high=a.high,
                    qualifier=a.qualifier,
                    scale=a.scale,
                    aggregate=a.aggregate,
                ),
            )
        )
    return found


def extract_claims(results: list[SearchResult], as_of: date, topic: str | None = None) -> ClaimExtraction:
    """`topic` is the investigation's question; defaults to each result's search query."""
    out = ClaimExtraction()
    for result in results:
        anchors = query_anchors(topic if topic is not None else result.query)
        result_text = f"{result.title} | {result.snippet}"
        found = _scan_field(result.title, EvidenceField.TITLE, "", anchors, result_text) + _scan_field(
            result.snippet, EvidenceField.SNIPPET, result.title, anchors, result_text
        )
        grouped: dict[tuple, list[_Found]] = {}
        for f in found:
            key = (f.claim_type, subject_key(f.qualifiers), f.attribute, f.value)
            grouped.setdefault(key, []).append(f)

        for (claim_type, subject, attribute, value), group in grouped.items():
            spans = []
            for f in group:
                s, e = _trimmed(f.text, *f.clause)
                spans.append(
                    EvidenceSpan(
                        id=new_id("ev"),
                        result_id=result.id,
                        source_id=result.source_id,
                        field=f.field,
                        text=f.text[s:e],
                        start=s,
                        end=e,
                    )
                )
            out.evidence_spans.extend(spans)
            primary = next((f for f in group if f.field == EvidenceField.SNIPPET), group[0])
            ambiguous = bool(primary.date and primary.date.ambiguous)
            out.claims.append(
                ExtractedClaim(
                    id=new_id("clm"),
                    source_id=result.source_id,
                    result_id=result.id,
                    evidence_span_ids=[s.id for s in spans],
                    claim_text=spans[group.index(primary)].text,
                    claim_type=claim_type,
                    subject=subject,
                    qualifiers=primary.qualifiers,
                    attribute=attribute,
                    value=value,
                    date=primary.date,
                    amount=primary.amount,
                    role=primary.role,
                    temporal_status=(
                        _temporal_status(primary.date.date_start, primary.date.date_end, as_of)
                        if primary.date
                        else TemporalStatus.NOT_APPLICABLE
                    ),
                    confidence=0.5 if ambiguous else 1.0,
                    **_eligibility(attribute, result, primary.qualifiers),
                )
            )
    return out


_PROCUREMENT = re.compile(
    r"\b(?:e[-\s]?tenders?|tenders?|tender\s+notice|rfp|rfq|request\s+for\s+(?:proposal|quotation)|"
    r"bid\s+(?:submission|due\s+date|opening)|pre[-\s]?bid|corrigendum|e[-\s]?procurement)\b",
    re.IGNORECASE,
)


def is_procurement_notice(result: SearchResult) -> bool:
    """Title, snippet or URL path identifies the page as a tender / RFP / bid notice."""
    path = re.sub(r"[-_/+.]", " ", result.link or "")
    return bool(_PROCUREMENT.search(f"{result.title} {result.snippet} {path}"))


def _eligibility(attribute: str, result: SearchResult, qualifiers: list[str]) -> dict:
    """Eligibility from the claim itself. Result relevance is applied later (claims.eligibility)."""
    if is_procurement_notice(result):
        state, reason = ClaimEligibility.PROCUREMENT_NOTICE, "page is a tender/RFP/bid notice; its dates are bid dates"
    elif attribute not in COMPARABLE_ATTRIBUTES:
        state, reason = ClaimEligibility.NOT_COMPARABLE_ATTRIBUTE, f"'{attribute}' values are not compared"
    elif result.source_id is None:
        state, reason = ClaimEligibility.NO_SOURCE, "result has no usable URL"
    elif is_ambiguous(qualifiers):
        state, reason = ClaimEligibility.SUBJECT_UNRESOLVED, "subject could not be pinned down: " + ", ".join(qualifiers)
    else:
        state, reason = ClaimEligibility.ELIGIBLE, "comparable attribute with a resolved subject"
    return {"eligibility": state, "eligibility_reason": reason, "comparable": state == ClaimEligibility.ELIGIBLE}
