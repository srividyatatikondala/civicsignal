from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from .claims import ClaimCluster, ExtractedClaim
from .common import DataMode
from .evidence import EvidenceSpan
from .findings import Finding, ValueOfficialSupport
from .independence import IndependenceSummary
from .search import FollowUpOutcome, SearchResult, SearchRun, Source

_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_LOCALE = re.compile(r"^[a-z]{2}(-[a-z]{2})?$")


class InvestigationStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"  # some searches or detectors failed
    FAILED = "failed"  # no usable search data


class DetectorStatus(str, Enum):
    OK = "ok"
    UNAVAILABLE = "unavailable"  # detector raised; investigation continued
    SKIPPED = "skipped"  # not applicable to this query
    NOT_RUN = "not_run"  # not implemented yet


class IntegrityStatus(str, Enum):
    """Qualitative summary derived from findings. Not a truth verdict."""

    NOT_ASSESSED = "NOT_ASSESSED"
    CLEAR = "CLEAR"
    MINOR_ISSUES = "MINOR_ISSUES"
    POTENTIALLY_STALE = "POTENTIALLY_STALE"
    CONFLICTING = "CONFLICTING"
    MIXED_EVIDENCE = "MIXED_EVIDENCE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"


class DetectorRun(BaseModel):
    detector: str
    status: DetectorStatus
    message: str = ""
    latency_ms: int = 0
    finding_count: int = 0


class LandscapeMetrics(BaseModel):
    """Deliberately separate counts — result count is not independent evidence."""

    total_search_results: int = 0
    results_by_type: dict[str, int] = Field(default_factory=dict)
    unique_urls: int = 0  # distinct raw links
    unique_sources: int = 0  # distinct canonical pages
    unique_domains: int = 0  # distinct registrable domains
    extracted_claims: int = 0
    claim_clusters: int = 0
    finding_events: int = 0
    # Independence view (None = duplication analysis did not run). Never replaces unique_sources.
    apparent_duplicate_groups: int | None = None  # groups of 2+ sources linked by shared wording
    independent_source_groups: int | None = None  # sources after merging related ones
    # Authority / relevance views (None = analyzer did not run)
    source_types: dict[str, int] | None = None
    official_sources: int | None = None  # official pages found (whether or not they address the topic)
    primary_sources: int | None = None  # official pages that ADDRESS the topic (official AND topic-relevant)
    relevance_levels: dict[str, int] | None = None  # all evidence (original + follow-up searches)
    # Original search vs targeted follow-up searches (roles from SearchRun.request.reason)
    base_results: int = 0  # result appearances from the original search
    base_relevance_levels: dict[str, int] | None = None  # relevance of the ORIGINAL search only
    follow_up_results: int = 0  # result appearances from follow-up searches
    follow_up_new_sources: int = 0  # distinct pages (canonical sources) first seen in a follow-up search
    follow_up_relevance_levels: dict[str, int] | None = None


class InvestigateRequest(BaseModel):
    query: str = Field(..., min_length=3, max_length=300)
    gl: str | None = Field(None, description="Country, e.g. 'in'")
    hl: str | None = Field(None, description="Interface language, e.g. 'en' or 'te'")
    max_results: int | None = Field(None, ge=1, le=100)
    follow_ups: bool = True  # allow reason-coded follow-up searches
    compare_hl: str | None = Field(None, description="Explicitly request a REGIONAL_COMPARE search in this language")

    @field_validator("query")
    @classmethod
    def _clean_query(cls, v: str) -> str:
        v = " ".join(v.split())
        if _CONTROL_CHARS.search(v):
            raise ValueError("query contains control characters")
        if len(v) < 3:
            raise ValueError("query is too short")
        return v

    @field_validator("gl", "hl", "compare_hl")
    @classmethod
    def _locale(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip().lower()
        if not _LOCALE.match(v):
            raise ValueError("must be a 2-letter code like 'in' or 'en'")
        return v


class Investigation(BaseModel):
    id: str
    query: str
    created_at: datetime
    completed_at: datetime | None = None
    as_of_date: date  # the "today" used for temporal reasoning
    status: InvestigationStatus
    integrity_status: IntegrityStatus = IntegrityStatus.NOT_ASSESSED
    data_modes: list[DataMode] = Field(default_factory=list)
    searches: list[SearchRun] = Field(default_factory=list)
    results: list[SearchResult] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    evidence_spans: list[EvidenceSpan] = Field(default_factory=list)
    claims: list[ExtractedClaim] = Field(default_factory=list)
    clusters: list[ClaimCluster] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    detector_runs: list[DetectorRun] = Field(default_factory=list)
    independence: IndependenceSummary | None = None
    official_support: list[ValueOfficialSupport] | None = None
    follow_ups: list[FollowUpOutcome] = Field(default_factory=list)  # per claim value: official sources stating it
    metrics: LandscapeMetrics = Field(default_factory=LandscapeMetrics)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class InvestigationSummary(BaseModel):
    id: str
    query: str
    created_at: datetime
    status: InvestigationStatus
    integrity_status: IntegrityStatus
    total_search_results: int
    unique_sources: int
    finding_events: int
