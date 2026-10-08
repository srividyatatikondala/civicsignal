"""
Evidence normalizer: raw SerpApi items -> SearchResults -> Sources + metrics.

Nothing is truncated or discarded. Items without a usable URL are kept as
results (source_id=None) and reported as warnings.
"""

from __future__ import annotations

from collections import Counter

from ..models import (
    LandscapeMetrics,
    ResultType,
    SearchResult,
    SerpApiResponse,
    Source,
    new_id,
)
from ..serp.parsing import ParsedItem
from .urls import canonical_url, host_of, registrable_domain

# Result types whose position reflects ranking in the main result list.
_RANKED_TYPES = {ResultType.ORGANIC, ResultType.NEWS}


def build_results(
    search_run_id: str, response: SerpApiResponse, items: list[ParsedItem]
) -> list[SearchResult]:
    results = []
    for item in items:
        host = host_of(item.link)
        results.append(
            SearchResult(
                id=new_id("res"),
                search_run_id=search_run_id,
                engine=response.request.engine,
                query=response.request.q,
                result_type=item.result_type,
                position=item.position,
                title=item.title,
                link=item.link,
                displayed_link=item.displayed_link,
                snippet=item.snippet,
                date=item.date,
                canonical_url=canonical_url(item.link),
                domain=host,
                registrable_domain=registrable_domain(host),
                retrieved_at=response.fetched_at,
                data_mode=response.data_mode,
                raw=item.raw,
            )
        )
    return results


def group_sources(results: list[SearchResult], existing: list[Source] | None = None) -> list[Source]:
    """
    Assign every result with a canonical URL to a Source (one per canonical
    URL), mutating result.source_id. Pass `existing` to merge results from a
    later search into sources discovered earlier.
    """
    by_canonical: dict[str, Source] = {s.canonical_url: s for s in (existing or [])}
    for r in results:
        if not r.canonical_url:
            continue
        source = by_canonical.get(r.canonical_url)
        if source is None:
            source = Source(
                id=new_id("src"),
                canonical_url=r.canonical_url,
                domain=r.domain or "unknown",
                registrable_domain=r.registrable_domain or "unknown",
            )
            by_canonical[r.canonical_url] = source
        r.source_id = source.id
        source.result_ids.append(r.id)
        if r.link and r.link not in source.urls:
            source.urls.append(r.link)
        # Prefer the organic listing's title; otherwise keep the first title seen.
        if r.title and (not source.title or r.result_type == ResultType.ORGANIC):
            source.title = r.title
        if r.result_type in _RANKED_TYPES and r.position is not None:
            if source.best_position is None or r.position < source.best_position:
                source.best_position = r.position
    return list(by_canonical.values())


def compute_metrics(
    results: list[SearchResult],
    sources: list[Source],
    claims: int = 0,
    clusters: int = 0,
    findings: int = 0,
    duplicate_groups: int | None = None,
    independent_groups: int | None = None,
) -> LandscapeMetrics:
    return LandscapeMetrics(
        total_search_results=len(results),
        results_by_type=dict(Counter(r.result_type.value for r in results)),
        unique_urls=len({r.link for r in results if r.link}),
        unique_sources=len(sources),
        unique_domains=len({s.registrable_domain for s in sources if s.registrable_domain != "unknown"}),
        extracted_claims=claims,
        claim_clusters=clusters,
        finding_events=findings,
        apparent_duplicate_groups=duplicate_groups,
        independent_source_groups=independent_groups,
    )
