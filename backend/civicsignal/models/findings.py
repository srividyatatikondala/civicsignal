"""
Detector findings. Every finding names its evidence (sources, claims, spans)
and uses deliberately hedged language — findings describe potential
information-integrity issues, never truth verdicts.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from .claims import TemporalRole, ValueGroup


class Strength(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FindingScope(str, Enum):
    WITHIN_SOURCE = "within_source"
    CROSS_SOURCE = "cross_source"
    LANDSCAPE = "landscape"  # about the result set as a whole


class _FindingBase(BaseModel):
    id: str
    detector: str
    title: str  # e.g. "Potentially stale deadline"
    explanation: str
    strength: Strength
    scope: FindingScope
    source_ids: list[str] = Field(default_factory=list)
    result_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    evidence_span_ids: list[str] = Field(default_factory=list)
    cluster_id: str | None = None
    verification_hint: str | None = None


class StalenessFinding(_FindingBase):
    kind: Literal["staleness"] = "staleness"
    as_of_date: str
    temporal_role: TemporalRole
    date_start: str
    date_end: str
    days_expired: int | None = None


class ContradictionFinding(_FindingBase):
    kind: Literal["contradiction"] = "contradiction"
    subject: str
    attribute: str
    claim_a_id: str  # representative claims from two disagreeing value groups
    claim_b_id: str
    conflicting_values: list[str]
    value_groups: list[ValueGroup] = Field(default_factory=list)  # every value and who states it


class DuplicationFinding(_FindingBase):
    """
    Relationship evidence between sources. relation is one of the
    RelationKind values, or "repeated_claim" when several sources state the
    same value but collapse into fewer apparently independent groups.
    """

    kind: Literal["duplication"] = "duplication"
    relation: str
    similarity: float  # headline metric for the relation (containment, title Jaccard, or groups/sources)
    method: str  # how it was measured, in plain words
    text_containment: float | None = None
    title_similarity: float | None = None
    longest_shared_run: int = 0
    shared_phrases: list[str] = Field(default_factory=list)
    differing_values: list[str] = Field(default_factory=list)  # similar text but different claim values
    supporting_sources: int | None = None  # repeated_claim only
    independent_groups: int | None = None  # repeated_claim only


class ValueOfficialSupport(BaseModel):
    cluster_id: str
    attribute: str
    value: str
    source_ids: list[str]
    official_source_ids: list[str]


class AuthorityFinding(_FindingBase):
    kind: Literal["authority"] = "authority"
    primary_sources: int = 0  # official AND topic-relevant sources
    total_sources: int = 0
    type_counts: dict[str, int] = Field(default_factory=dict)
    value_support: list[ValueOfficialSupport] = Field(default_factory=list)


class RelevanceFinding(_FindingBase):
    kind: Literal["relevance"] = "relevance"
    level: Literal["high", "medium", "low", "off_topic"]  # the weakest level among the listed results
    level_counts: dict[str, int] = Field(default_factory=dict)
    total_results: int = 0


Finding = Annotated[
    StalenessFinding
    | ContradictionFinding
    | DuplicationFinding
    | AuthorityFinding
    | RelevanceFinding,
    Field(discriminator="kind"),
]
