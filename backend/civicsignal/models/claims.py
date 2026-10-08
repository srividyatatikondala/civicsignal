"""
Typed claims and claim clusters.

A claim is (subject, attribute, value) with a value type, a temporal role
(for dates), the source it came from, and the exact evidence spans it rests
on. Values are stored as intervals so precision is respected: "July 2026"
is [Jul 1, Jul 31]; "about ₹4,000 crore" is [3,600, 4,400] crore.

A cluster groups claims with the same subject key and attribute. Claims in
a cluster are partitioned into value groups (overlapping intervals agree).
More than one value group = conflicting claims. The same structure covers
both cases:
  * within-source contradiction — groups disagree inside one source
  * cross-source conflict       — groups are supported by different sources
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import BaseModel, Field


class ClaimType(str, Enum):
    """Value type of a claim."""

    DATE = "date"
    AMOUNT = "amount"
    OTHER = "other"


class TemporalRole(str, Enum):
    DEADLINE = "deadline"
    EXPECTED_EVENT = "expected_event"
    PAST_EVENT = "past_event"
    HISTORICAL_REFERENCE = "historical_reference"
    OPEN_ENDED = "open_ended"
    PUBLICATION = "publication"
    UPDATED = "updated"
    ELIGIBILITY_CUTOFF = "eligibility_cutoff"  # e.g. "born on or before 31 Dec 2008" — a rule, not a deadline
    UNKNOWN = "unknown"


class TemporalStatus(str, Enum):
    """Where a date claim's period sits relative to the investigation's as_of_date."""

    PASSED = "passed"  # whole period ended before as_of_date
    CURRENT = "current"  # as_of_date falls inside the period
    UPCOMING = "upcoming"  # period starts after as_of_date
    NOT_APPLICABLE = "not_applicable"  # non-date claims


class ClaimEligibility(str, Enum):
    """Whether a claim may take part in conflict comparison, and if not, why. Claims are never deleted."""

    ELIGIBLE = "eligible"
    NOT_COMPARABLE_ATTRIBUTE = "not_comparable_attribute"  # historical/publication/unknown dates, aggregates, ...
    SUBJECT_UNRESOLVED = "subject_unresolved"  # ambiguous ordinal or unresolved entity
    NO_SOURCE = "no_source"  # result has no usable URL
    PROCUREMENT_NOTICE = "procurement_notice"  # tender/RFP/bid page: its dates are bid dates, not topic claims
    RESULT_WEAKLY_RELEVANT = "result_weakly_relevant"  # its result was rated LOW relevance
    RESULT_OFF_TOPIC = "result_off_topic"  # its result was rated OFF_TOPIC
    RELEVANCE_UNKNOWN = "relevance_unknown"  # relevance was not assessed; not silently compared


class DatePrecision(str, Enum):
    DAY = "day"
    MONTH = "month"
    MONTH_RANGE = "month_range"
    DAY_RANGE = "day_range"


class DateEvidence(BaseModel):
    matched_text: str
    precision: DatePrecision
    date_start: date
    date_end: date
    ambiguous: bool = False  # e.g. 05/06/2026 where DD/MM and MM/DD are both valid


class AmountEvidence(BaseModel):
    matched_text: str  # e.g. "about Rs 4,000 crore"
    currency: str = "INR"
    value: float  # stated number with scale applied, in rupees
    low: float  # interval implied by the qualifier ("about" -> ±10%)
    high: float | None  # None = unbounded ("over ₹2,650 crore")
    qualifier: str | None = None  # "about", "over", "up to", ...
    scale: str | None = None  # "crore", "lakh", ...
    aggregate: bool = False  # crore/lakh-scale totals: extracted, not compared


class ExtractedClaim(BaseModel):
    id: str
    source_id: str | None
    result_id: str
    evidence_span_ids: list[str] = Field(default_factory=list)
    claim_text: str  # the clause the claim was read from (exact text)
    claim_type: ClaimType  # value type
    subject: str = ""  # normalized subject key; "" = the investigation topic itself
    qualifiers: list[str] = Field(default_factory=list)  # e.g. ["installment:23"]
    attribute: str  # e.g. "deadline", "expected_date", "fee"
    value: str  # normalized display value, e.g. "2026-06-14" or "INR 10"
    date: DateEvidence | None = None
    amount: AmountEvidence | None = None
    role: TemporalRole = TemporalRole.UNKNOWN
    temporal_status: TemporalStatus = TemporalStatus.NOT_APPLICABLE
    comparable: bool = False  # True only when eligibility == ELIGIBLE
    eligibility: ClaimEligibility = ClaimEligibility.NOT_COMPARABLE_ATTRIBUTE
    eligibility_reason: str = ""
    confidence: float = Field(1.0, ge=0, le=1)
    extractor: str = "rules"  # "rules" | "llm:<provider>"


class ValueGroup(BaseModel):
    """Claims in a cluster whose values agree (overlapping intervals)."""

    value: str  # representative display value
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class ClaimCluster(BaseModel):
    id: str
    subject: str
    attribute: str
    claim_type: ClaimType
    claim_ids: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    value_groups: list[ValueGroup] = Field(default_factory=list)
    has_conflict: bool = False
