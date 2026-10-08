"""
Source relationship / independence evidence.

Result appearances, URLs, sources (canonical pages) and domains are counted
separately elsewhere. This module adds a further, separate view: which
sources appear related (shared wording, same publisher) and therefore may
not be independent of each other. It never replaces the source count.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class RelationKind(str, Enum):
    NEAR_IDENTICAL = "near_identical"  # most distinctive wording shared
    SUBSTANTIAL_OVERLAP = "substantial_overlap"  # large shared passages
    RELATED_TITLE = "related_title"  # near-identical headlines, snippets inconclusive (does not merge groups)
    SAME_PUBLISHER = "same_publisher"  # different pages on one publisher's domain


MERGING_RELATIONS = {RelationKind.NEAR_IDENTICAL, RelationKind.SUBSTANTIAL_OVERLAP, RelationKind.SAME_PUBLISHER}


class SourceRelationship(BaseModel):
    source_a_id: str
    source_b_id: str
    kind: RelationKind
    text_containment: float | None = None  # share of the shorter text's distinctive 3-word phrases found in the other
    longest_shared_run: int = 0  # longest identical word sequence
    title_similarity: float | None = None  # Jaccard over distinctive title words
    shared_phrases: list[str] = Field(default_factory=list)
    shared_domain: str | None = None
    merges_independence: bool = False


class SourceGroup(BaseModel):
    """Sources that appear related; counts as ONE apparently independent group."""

    id: str
    source_ids: list[str]
    relation_kinds: list[RelationKind] = Field(default_factory=list)


class ClaimSupport(BaseModel):
    """How many apparently independent groups stand behind one value of one claim cluster."""

    cluster_id: str
    value: str
    source_ids: list[str]
    independent_groups: int


class IndependenceSummary(BaseModel):
    relationships: list[SourceRelationship] = Field(default_factory=list)
    groups: list[SourceGroup] = Field(default_factory=list)
    claim_support: list[ClaimSupport] = Field(default_factory=list)
