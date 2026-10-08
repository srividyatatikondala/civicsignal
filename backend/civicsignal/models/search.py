"""
Search-layer models.

Key distinction kept throughout CivicSignal:
  * SearchResult — one *appearance* of a page in one search (has a position).
  * Source       — one page, identified by canonical URL. The same Source can
                   back many SearchResults (several searches, answer box +
                   organic listing, ...). Sources, not results, are what count
                   as independent evidence.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from .common import DataMode


class SearchReason(str, Enum):
    """Why a search was performed. Every SerpApi call must carry one."""

    BASE_QUERY = "base_query"
    PRIMARY_SOURCE_LOOKUP = "primary_source_lookup"
    RECENCY_CONTRAST = "recency_contrast"
    NEWS_CHECK = "news_check"
    REGIONAL_COMPARE = "regional_compare"


class ResultType(str, Enum):
    ORGANIC = "organic"
    ANSWER_BOX = "answer_box"
    RELATED_QUESTION = "related_question"
    TOP_STORY = "top_story"
    NEWS = "news"


class SourceType(str, Enum):
    """What kind of publisher a source is. Context for evidence — not a measure of truth or quality."""

    OFFICIAL = "official"  # government / official institution domains
    ESTABLISHED_NEWS = "established_news"
    TERTIARY = "tertiary"  # blogs, explainers, aggregators, job/result portals, finance explainers
    USER_GENERATED = "user_generated"  # video/social/forum platforms
    UNKNOWN = "unknown"


class RelevanceLevel(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    OFF_TOPIC = "off_topic"


class SearchRequest(BaseModel):
    """A SerpApi request, minus the API key (which is injected at call time only)."""

    engine: str = "google"
    q: str
    params: dict[str, str | int] = Field(default_factory=dict)
    reason: SearchReason = SearchReason.BASE_QUERY
    reason_detail: str = ""

    def cache_key(self) -> str:
        """Stable hash of everything that determines the response."""
        payload = json.dumps(
            {"engine": self.engine, "q": self.q, "params": self.params},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def to_query_params(self) -> dict[str, str | int]:
        return {"engine": self.engine, "q": self.q, **self.params}


class SerpApiResponse(BaseModel):
    """Raw SerpApi JSON plus provenance. `raw` has secrets scrubbed."""

    request: SearchRequest
    raw: dict[str, Any]
    data_mode: DataMode
    fetched_at: datetime  # when the data was originally retrieved from SerpApi
    latency_ms: int = 0
    serpapi_search_id: str | None = None
    fixture_kind: str | None = None  # "captured" | "reconstructed" (mock mode only)
    notes: list[str] = Field(default_factory=list)


class SearchRunStatus(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    FAILED = "failed"
    SKIPPED = "skipped"  # planned follow-up not executed (e.g. mock mode without a captured fixture)


class SearchRun(BaseModel):
    """One executed search inside an investigation (raw response stored separately)."""

    id: str
    investigation_id: str
    request: SearchRequest
    status: SearchRunStatus
    data_mode: DataMode | None = None
    fixture_kind: str | None = None
    fetched_at: datetime | None = None
    latency_ms: int = 0
    result_count: int = 0
    serpapi_search_id: str | None = None
    error: str | None = None
    notes: list[str] = Field(default_factory=list)
    # follow-up provenance (empty for the base search)
    parent_search_id: str | None = None
    question: str = ""  # what this search is trying to resolve
    triggered_by: list[str] = Field(default_factory=list)  # evidence from earlier results that justified it
    planned_at: datetime | None = None


class FollowUpOutcome(BaseModel):
    """What a follow-up search contributed. Describes evidence found — never a truth verdict."""

    search_run_id: str
    reason: SearchReason
    query: str
    engine: str
    params: dict[str, str | int] = Field(default_factory=dict)  # e.g. {"tbs": "qdr:m"} for recency
    question: str
    triggered_by: list[str] = Field(default_factory=list)
    status: SearchRunStatus
    results_added: int = 0
    new_source_ids: list[str] = Field(default_factory=list)
    official_relevant_source_ids: list[str] = Field(default_factory=list)
    # Outcome in evidence terms. PRIMARY_SOURCE_LOOKUP: support_found | official_found_not_addressing_claims |
    # no_useful_official_result | no_results | failed | skipped. NEWS_CHECK / REGIONAL_COMPARE:
    # additional_relevant_evidence | no_additional_relevant_evidence | ... RECENCY_CONTRAST:
    # newer_relevant_evidence | no_newer_relevant_evidence | ...
    outcome_code: str = ""
    relevant_results: int = 0  # results from this search rated high/medium relevance
    # PRIMARY_SOURCE_LOOKUP only (None = not a primary lookup, or not executed):
    official_topic_pages_found: bool | None = None  # an official page that addresses the topic was returned
    primary_source_support_found: bool | None = None  # an official page from this search STATES a claim value
    values_with_official_support: list[str] = Field(default_factory=list)
    values_not_addressed_by_official_sources: list[str] = Field(default_factory=list)
    summary: str = ""


class SearchResult(BaseModel):
    """One result item exactly as retrieved, plus normalized identifiers. Never truncated."""

    id: str
    search_run_id: str
    source_id: str | None = None  # None when the item has no usable URL
    engine: str
    query: str
    result_type: ResultType
    position: int | None = None
    title: str = ""
    link: str | None = None
    displayed_link: str | None = None
    snippet: str = ""
    date: str | None = None  # SerpApi's displayed date string, unparsed
    canonical_url: str | None = None
    domain: str | None = None  # host without www., e.g. "uidai.gov.in"
    registrable_domain: str | None = None  # e.g. "gov.in"-aware: "uidai.gov.in"
    retrieved_at: datetime
    data_mode: DataMode
    relevance: RelevanceLevel | None = None  # set by the relevance analyzer
    relevance_signals: list[str] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)


class Source(BaseModel):
    """A distinct page (canonical URL) across all searches in an investigation."""

    id: str
    canonical_url: str
    domain: str
    registrable_domain: str
    urls: list[str] = Field(default_factory=list)  # distinct raw links seen for this page
    result_ids: list[str] = Field(default_factory=list)
    best_position: int | None = None  # best organic/news position observed
    title: str = ""
    source_type: SourceType = SourceType.UNKNOWN  # set by the authority analyzer
    type_signals: list[str] = Field(default_factory=list)  # why that type was assigned
