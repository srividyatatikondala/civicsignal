"""
Investigation report: a UI-ready, evidence-linked view derived from an
Investigation. Nothing here is stored; it is rebuilt from the investigation
on request, so it can never disagree with the underlying evidence.
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field


class StatusBlock(BaseModel):
    code: str  # IntegrityStatus value
    label: str  # e.g. "Conflicting claims"
    explanation: str


class DataNotice(BaseModel):
    mode: str  # "live" | "cached" | "demo" | "unavailable"
    label: str  # e.g. "CAPTURED SERPAPI DATA"
    detail: str


class LandscapeView(BaseModel):
    result_appearances: int
    unique_urls: int
    unique_sources: int
    unique_domains: int
    independent_source_groups: int | None
    claims_extracted: int
    claims_compared: int
    official_topic_sources: int | None  # official pages that ADDRESS the topic
    official_pages_found: int | None = None  # all official pages found (addressing the topic or not)
    source_types: dict[str, int] = Field(default_factory=dict)
    relevance: dict[str, int] = Field(default_factory=dict)  # all evidence
    # original search vs targeted investigation searches
    includes_follow_ups: bool = False
    base_results: int = 0
    base_relevance: dict[str, int] = Field(default_factory=dict)
    follow_up_results: int = 0
    follow_up_new_sources: int = 0  # distinct pages first seen in a follow-up search
    follow_up_relevant_results: int = 0
    follow_up_relevance: dict[str, int] = Field(default_factory=dict)


class EvidenceItem(BaseModel):
    source_id: str | None
    domain: str | None
    url: str | None  # http/https only
    title: str
    source_type: str | None
    search_position: int | None
    found_by: str  # search reason, e.g. "base_query", "primary_source_lookup"
    field: str | None = None  # "title" | "snippet"
    quote: str | None = None  # exact evidence text
    retrieved_at: datetime | None = None


class IssueCard(BaseModel):
    finding_id: str
    category: str  # staleness | conflicting_claims | repeated_content | primary_source | relevance
    category_label: str
    severity: str  # high | medium | low
    title: str
    summary: str
    evidence: list[EvidenceItem] = Field(default_factory=list)
    how_to_verify: str | None = None


class MapSource(BaseModel):
    source_id: str
    domain: str
    title: str = ""
    url: str | None
    source_type: str
    official: bool


class MapEvidence(BaseModel):
    """One piece of value-specific evidence: the exact text a claim for this value was read from."""

    claim_id: str
    result_id: str
    evidence_span_id: str
    source_id: str | None
    search_run_id: str
    domain: str | None
    field: str  # "title" | "snippet"
    excerpt: str  # exact prefix of the evidence span text (word boundary, <= ~220 chars)
    truncated: bool = False
    found_by: str  # search role, e.g. "base_query"
    found_by_label: str  # e.g. "original search", "official-source lookup"


class MapValue(BaseModel):
    value: str  # normalized claim value, exactly as extracted (e.g. "2026-06-01/2026-06-30")
    value_label: str = ""  # display form of the same value (e.g. "Jun 2026"); formatting only
    sources: list[MapSource]  # all sources stating the value (kept for compatibility)
    independent_groups: int | None
    stated_by_official_source: bool
    value_type: str = ""  # "date" | "amount"
    temporal_roles: list[str] = Field(default_factory=list)  # e.g. ["deadline"], ["expected date"]
    temporal_status: str | None = None  # "date has passed" | "upcoming" | "current" | "mixed"
    potentially_stale: bool = False
    stale_finding_ids: list[str] = Field(default_factory=list)
    official_sources: list[MapSource] = Field(default_factory=list)  # official sources that STATE this value
    other_sources: list[MapSource] = Field(default_factory=list)  # non-official sources that state it
    official_support_reason: str = ""
    evidence: list[MapEvidence] = Field(default_factory=list)


class MapEntry(BaseModel):
    cluster_id: str
    subject: str
    attribute: str
    has_conflict: bool
    values: list[MapValue]
    value_type: str = ""
    conflict_explanation: str | None = None  # neutral; only when values conflict
    finding_ids: list[str] = Field(default_factory=list)  # contradiction + staleness findings for this claim


class TrailStep(BaseModel):
    """One observable step of the investigation (an audit trail, not reasoning)."""

    text: str
    kind: str  # "search" | "gap" | "action" | "outcome" | "result"


class FollowUpView(BaseModel):
    reason: str
    reason_label: str
    query: str
    engine: str
    params: dict[str, str | int] = Field(default_factory=dict)
    question: str
    triggered_by: list[str] = Field(default_factory=list)
    status: str
    outcome_code: str = ""
    outcome_label: str = ""  # plain-language outcome (owned by the backend)
    results_added: int
    relevant_results: int = 0
    new_sources: int = 0
    outcome: str


class VerifySource(BaseModel):
    domain: str
    url: str | None
    title: str
    states_values: list[str] = Field(default_factory=list)  # claim values this official source states
    why_relevant: str = ""
    found_by_label: str = ""


class InvestigationReport(BaseModel):
    investigation_id: str
    question: str
    as_of_date: date
    created_at: datetime
    status: StatusBlock
    data_notice: DataNotice
    landscape: LandscapeView
    issue_counts: dict[str, int] = Field(default_factory=dict)
    issues: list[IssueCard] = Field(default_factory=list)
    evidence_map: list[MapEntry] = Field(default_factory=list)
    excluded_claims: dict[str, int] = Field(default_factory=dict)  # eligibility reason -> count
    follow_ups: list[FollowUpView] = Field(default_factory=list)
    trail: list[TrailStep] = Field(default_factory=list)
    where_to_verify: list[VerifySource] = Field(default_factory=list)
    where_to_verify_note: str
    checks_run: dict[str, str] = Field(default_factory=dict)  # detector -> status
    method_notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
