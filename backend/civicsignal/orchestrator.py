"""
Investigation orchestrator.

Current pipeline:  base search -> parse -> normalize -> group sources ->
relevance -> typed claim extraction (+ relevance eligibility) -> staleness ->
claim clustering -> contradiction -> duplication/independence -> source
type/authority -> metrics -> integrity status -> persist.

Duplication findings are relationship evidence; they do not change the
integrity status on their own (repeated content is not evidence of error).

Each analysis step is isolated: if one raises, it is reported as
`unavailable` and the investigation continues. Detectors not yet
implemented are reported as `not_run`, and with no findings the status
stays NOT_ASSESSED (never CLEAR) — the report never implies analysis that
did not happen.

Follow-ups: after the first analysis, the planner (planner.py) may request
up to MAX_FOLLOW_UP_SEARCHES reason-coded searches. Their results join the
same evidence model and the whole analysis runs again over all results.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Callable
from datetime import date
from typing import Any

from .config import Settings
from .db import Repository
from .logging_setup import log_event
from .models import (
    DetectorRun,
    DetectorStatus,
    IntegrityStatus,
    InvestigateRequest,
    Investigation,
    InvestigationStatus,
    ResultType,
    SearchReason,
    SearchRequest,
    SearchRun,
    SearchRunStatus,
    SourceType,
    new_id,
    utc_now,
)
from .claims import apply_relevance_eligibility, cluster_claims, extract_claims
from .detectors import authority, contradiction, duplication, relevance, staleness
from .follow_ups import summarize_follow_ups
from .normalize import build_results, compute_metrics, group_sources
from .planner import PlannedSearch, plan_follow_ups
from .serp import SerpApiClient, SerpApiError, SerpApiFixtureNotFound, parse_response

logger = logging.getLogger("civicsignal.orchestrator")

PLANNED_DETECTORS = ["staleness", "contradiction", "duplication", "authority", "relevance"]


OFF_TOPIC_INSUFFICIENT_SHARE = 0.5
# Relevant (high/medium) follow-up results needed to count as evidence recovered for an off-topic search
MIN_RELEVANT_RESULTS = 3
_RANKED = {ResultType.ORGANIC, ResultType.NEWS, ResultType.TOP_STORY}


def cap_ranked_items(items: list, limit: int) -> list:
    """
    Keep at most `limit` results of EACH ranked block (organic, news, top stories) per search,
    in their original order. Engines such as google_news ignore `num` and can return 100 stories;
    the raw response is still stored in full. Answer box and "People also ask" items are not capped.
    """
    kept, seen = [], Counter()
    for item in items:
        if item.result_type in _RANKED:
            seen[item.result_type] += 1
            if seen[item.result_type] > limit:
                continue
        kept.append(item)
    return kept


def role_metrics(inv: Investigation) -> dict[str, Any]:
    """Original-search vs follow-up counts, using each result's search role (SearchRun.request.reason)."""
    base_ids = {s.id for s in inv.searches if s.request.reason == SearchReason.BASE_QUERY}
    base = [r for r in inv.results if r.search_run_id in base_ids]
    follow = [r for r in inv.results if r.search_run_id not in base_ids]
    base_sources = {r.source_id for r in base if r.source_id}

    def levels(rs):
        counted = Counter(r.relevance.value for r in rs if r.relevance is not None)
        return dict(counted) if counted else None

    return {
        "base_results": len(base),
        "base_relevance_levels": levels(base),
        "follow_up_results": len(follow),
        "follow_up_new_sources": len({r.source_id for r in follow if r.source_id} - base_sources),
        "follow_up_relevance_levels": levels(follow),
    }


def derive_integrity_status(inv: Investigation) -> IntegrityStatus:
    """
    Precedence: INSUFFICIENT_EVIDENCE (no data) > CONFLICTING > POTENTIALLY_STALE >
    INSUFFICIENT_EVIDENCE (the ORIGINAL search is half or more off-topic AND targeted
    follow-up searches recovered fewer than MIN_RELEVANT_RESULTS relevant results — so
    base-only investigations behave exactly as before) > MINOR_ISSUES (only duplication/authority/relevance findings) >
    NOT_ASSESSED (no findings). Follow-up searches can recover evidence for an off-topic
    original search; off-topic follow-up results never make the outcome worse.
    Derived from findings only. There is no CLEAR status: the absence of
    detected issues is not verification.
    """
    if inv.status == InvestigationStatus.FAILED or not inv.results:
        return IntegrityStatus.INSUFFICIENT_EVIDENCE
    if any(f.kind == "contradiction" for f in inv.findings):
        return IntegrityStatus.CONFLICTING
    if any(f.kind == "staleness" for f in inv.findings):
        return IntegrityStatus.POTENTIALLY_STALE
    base = inv.metrics.base_relevance_levels or inv.metrics.relevance_levels or {}
    if base and base.get("off_topic", 0) / sum(base.values()) >= OFF_TOPIC_INSUFFICIENT_SHARE:
        recovered = inv.metrics.follow_up_relevance_levels or {}
        if recovered.get("high", 0) + recovered.get("medium", 0) < MIN_RELEVANT_RESULTS:
            return IntegrityStatus.INSUFFICIENT_EVIDENCE
    if inv.findings:
        return IntegrityStatus.MINOR_ISSUES
    return IntegrityStatus.NOT_ASSESSED


class Investigator:
    def __init__(
        self,
        settings: Settings,
        client: SerpApiClient,
        repository: Repository,
        today: Callable[[], date] = date.today,
    ):
        self.settings = settings
        self.client = client
        self.repository = repository
        self._today = today

    def _base_request(self, req: InvestigateRequest) -> SearchRequest:
        num = min(req.max_results or self.settings.max_results_per_search, self.settings.max_results_per_search)
        return SearchRequest(
            engine="google",
            q=req.query,
            params={
                "gl": req.gl or self.settings.default_gl,
                "hl": req.hl or self.settings.default_hl,
                "num": num,
            },
            reason=SearchReason.BASE_QUERY,
            reason_detail="User's original question.",
        )

    async def investigate(self, req: InvestigateRequest) -> Investigation:
        started = time.perf_counter()
        inv = Investigation(
            id=new_id("inv"),
            query=req.query,
            created_at=utc_now(),
            as_of_date=self._today(),
            status=InvestigationStatus.FAILED,
        )
        log_event(logger, "investigation_started", investigation_id=inv.id, query=req.query)

        raw_responses: dict[str, dict[str, Any]] = {}
        plan = [self._base_request(req)][: self.settings.max_searches_per_investigation]

        for request in plan:
            run, raw = await self._run_search(inv, request)
            inv.searches.append(run)
            if raw is not None:
                raw_responses[run.id] = raw

        inv.sources = group_sources(inv.results)
        unlinked = sum(1 for r in inv.results if r.source_id is None)
        if unlinked:
            inv.warnings.append(f"{unlinked} result(s) had no usable URL and are not counted as sources.")

        implemented, extra_metrics = self._analyze(inv)

        if req.follow_ups and self.settings.planner_enabled:
            planned = self._plan(inv, req, extra_metrics)
            if planned:
                before = len(inv.results)
                for p in planned:
                    run, raw = await self._run_search(inv, p.request, p)
                    inv.searches.append(run)
                    if raw is not None:
                        raw_responses[run.id] = raw
                new_results = inv.results[before:]
                if new_results:  # follow-up evidence joins the same model; analyse everything again
                    inv.sources = group_sources(new_results, existing=inv.sources)
                    implemented, extra_metrics = self._analyze(inv)
                inv.follow_ups = summarize_follow_ups(inv)

        inv.detector_runs = implemented + [
            DetectorRun(detector=name, status=DetectorStatus.NOT_RUN, message="Not implemented yet.")
            for name in PLANNED_DETECTORS
            if name not in {d.detector for d in implemented}
        ]
        inv.metrics = compute_metrics(
            inv.results,
            inv.sources,
            claims=len(inv.claims),
            clusters=len(inv.clusters),
            findings=len(inv.findings),
            duplicate_groups=duplication.duplicate_group_count(inv.independence) if inv.independence else None,
            independent_groups=len(inv.independence.groups) if inv.independence else None,
        )
        inv.metrics = inv.metrics.model_copy(update={**extra_metrics, **role_metrics(inv)})
        inv.data_modes = sorted({r.data_mode for r in inv.searches if r.data_mode}, key=lambda m: m.value)

        executed = [r for r in inv.searches if r.status != SearchRunStatus.SKIPPED]
        succeeded = [r for r in executed if r.status != SearchRunStatus.FAILED]
        if not succeeded:
            inv.status = InvestigationStatus.FAILED
        elif len(succeeded) < len(executed):
            inv.status = InvestigationStatus.PARTIAL
        else:
            inv.status = InvestigationStatus.COMPLETED
        inv.integrity_status = derive_integrity_status(inv)
        inv.warnings = list(dict.fromkeys(inv.warnings))  # analysis may run twice
        inv.completed_at = utc_now()

        await asyncio.to_thread(self.repository.save_investigation, inv, raw_responses)
        log_event(
            logger,
            "investigation_finished",
            investigation_id=inv.id,
            status=inv.status.value,
            searches=len(inv.searches),
            total_results=inv.metrics.total_search_results,
            unique_sources=inv.metrics.unique_sources,
            unique_domains=inv.metrics.unique_domains,
            claims=inv.metrics.extracted_claims,
            findings=inv.metrics.finding_events,
            data_modes=[m.value for m in inv.data_modes],
            errors=len(inv.errors),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return inv

    def _unavailable(self, inv: Investigation, name: str, exc: Exception, started: float) -> DetectorRun:
        logger.exception("detector_failed", extra={"fields": {"investigation_id": inv.id, "detector": name}})
        inv.warnings.append(f"The {name} analysis was unavailable for this investigation.")
        return DetectorRun(
            detector=name,
            status=DetectorStatus.UNAVAILABLE,
            message=f"{type(exc).__name__}: {exc}",
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _analyze(self, inv: Investigation) -> tuple[list[DetectorRun], dict[str, Any]]:
        """(Re)run the full analysis over all current results, discarding any previous analysis output."""
        inv.evidence_spans, inv.claims, inv.clusters, inv.findings = [], [], [], []
        inv.independence, inv.official_support = None, None
        for r in inv.results:
            r.relevance, r.relevance_signals = None, []
        return self._run_analysis(inv)

    def _plan(self, inv: Investigation, req: InvestigateRequest, extra: dict[str, Any]) -> list[PlannedSearch]:
        """Plan follow-ups from the first-pass analysis. A planner failure never fails the investigation."""
        executed = sum(1 for s in inv.searches if s.status != SearchRunStatus.SKIPPED)
        budget = min(self.settings.max_follow_up_searches, self.settings.max_searches_per_investigation - executed)
        snapshot = inv.model_copy(update={"metrics": inv.metrics.model_copy(update=extra)})
        try:
            planned = plan_follow_ups(snapshot, budget, compare_hl=req.compare_hl)
        except Exception:  # noqa: BLE001
            logger.exception("planner_failed", extra={"fields": {"investigation_id": inv.id}})
            inv.warnings.append("The follow-up search planner was unavailable; only the initial search was analysed.")
            return []
        for p in planned:
            log_event(logger, "follow_up_planned", investigation_id=inv.id, reason=p.request.reason.value,
                      q=p.request.q, engine=p.request.engine, triggered_by=p.triggered_by)
        return planned

    def _run_analysis(self, inv: Investigation) -> tuple[list[DetectorRun], dict[str, Any]]:
        """
        relevance -> claim extraction (+ relevance eligibility) -> staleness -> clustering/contradiction
        -> duplication -> authority. Each failure is isolated. Returns detector runs and extra metrics.
        """
        runs: list[DetectorRun] = []
        extra: dict[str, Any] = {}
        runs.append(self._run_relevance(inv, extra))

        started = time.perf_counter()
        try:
            extraction = extract_claims(inv.results, inv.as_of_date, topic=inv.query)
        except Exception as exc:  # noqa: BLE001 - without claims, neither claim detector can run
            for name in (staleness.DETECTOR_NAME, contradiction.DETECTOR_NAME):
                runs.append(self._unavailable(inv, name, exc, started))
            runs.append(self._run_duplication(inv))  # needs only source text
            runs.append(self._run_authority(inv, extra))
            return runs, extra
        inv.evidence_spans.extend(extraction.evidence_spans)
        inv.claims.extend(extraction.claims)
        # Claims from weak/off-topic (or unassessed) results stay as evidence but are not compared.
        apply_relevance_eligibility(inv.claims, inv.results)

        started = time.perf_counter()
        config = staleness.StalenessConfig(
            high_days=self.settings.staleness_high_days,
            high_max_position=self.settings.staleness_high_max_position,
            medium_days=self.settings.staleness_medium_days,
        )
        try:
            stale = staleness.find_stale(inv.claims, inv.results, inv.as_of_date, config)
        except Exception as exc:  # noqa: BLE001 - detector isolation is the point
            runs.append(self._unavailable(inv, staleness.DETECTOR_NAME, exc, started))
        else:
            inv.findings.extend(stale)
            date_claims = sum(1 for c in inv.claims if c.claim_type.value == "date")
            runs.append(
                DetectorRun(
                    detector=staleness.DETECTOR_NAME,
                    status=DetectorStatus.OK,
                    message=f"{date_claims} date claim(s); {len(stale)} potentially stale finding(s) on "
                    f"{len({s for f in stale for s in f.source_ids})} source(s).",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    finding_count=len(stale),
                )
            )

        started = time.perf_counter()
        try:
            inv.clusters = cluster_claims(inv.claims)
            conflicts = contradiction.detect_contradictions(inv.clusters, inv.claims)
        except Exception as exc:  # noqa: BLE001
            runs.append(self._unavailable(inv, contradiction.DETECTOR_NAME, exc, started))
        else:
            inv.findings.extend(conflicts)
            within = sum(1 for f in conflicts if f.scope.value == "within_source")
            excluded = Counter(c.eligibility.value for c in inv.claims if c.eligibility.value.startswith(("result_", "relevance_")))
            runs.append(
                DetectorRun(
                    detector=contradiction.DETECTOR_NAME,
                    status=DetectorStatus.OK,
                    message=f"{sum(c.comparable for c in inv.claims)} comparable claim(s) in "
                    f"{len(inv.clusters)} cluster(s); {within} within-source and "
                    f"{len(conflicts) - within} cross-source conflict(s)"
                    + (f"; excluded by relevance: {dict(excluded)}" if excluded else "")
                    + ".",
                    latency_ms=int((time.perf_counter() - started) * 1000),
                    finding_count=len(conflicts),
                )
            )
        runs.append(self._run_duplication(inv))
        runs.append(self._run_authority(inv, extra))
        return runs, extra

    def _run_relevance(self, inv: Investigation, extra: dict[str, Any]) -> DetectorRun:
        started = time.perf_counter()
        try:
            base_ids = {s.id for s in inv.searches if s.request.reason == SearchReason.BASE_QUERY}
            rel = relevance.analyze_relevance(inv.results, inv.query, base_run_ids=base_ids)
        except Exception as exc:  # noqa: BLE001
            for r in inv.results:  # never leave partial levels behind
                r.relevance, r.relevance_signals = None, []
            return self._unavailable(inv, relevance.DETECTOR_NAME, exc, started)
        inv.findings.extend(rel.findings)
        inv.evidence_spans.extend(rel.evidence_spans)
        extra["relevance_levels"] = rel.counts()
        return DetectorRun(
            detector=relevance.DETECTOR_NAME,
            status=DetectorStatus.OK,
            message="Result relevance: " + ", ".join(f"{k}={v}" for k, v in sorted(rel.counts().items())),
            latency_ms=int((time.perf_counter() - started) * 1000),
            finding_count=len(rel.findings),
        )

    def _run_authority(self, inv: Investigation, extra: dict[str, Any]) -> DetectorRun:
        started = time.perf_counter()
        try:
            findings, support, type_counts, primary = authority.analyze_authority(
                inv.results, inv.sources, inv.claims, inv.clusters, inv.query
            )
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(inv, authority.DETECTOR_NAME, exc, started)
        inv.findings.extend(findings)
        inv.official_support = support
        extra["source_types"] = type_counts
        extra["official_sources"] = type_counts.get(SourceType.OFFICIAL.value, 0)
        extra["primary_sources"] = primary
        return DetectorRun(
            detector=authority.DETECTOR_NAME,
            status=DetectorStatus.OK,
            message=f"Source types: {', '.join(f'{k}={v}' for k, v in sorted(type_counts.items()))}; "
            f"{primary} official topic-relevant source(s).",
            latency_ms=int((time.perf_counter() - started) * 1000),
            finding_count=len(findings),
        )

    def _run_duplication(self, inv: Investigation) -> DetectorRun:
        started = time.perf_counter()
        try:
            dup = duplication.detect_duplication(inv.results, inv.sources, inv.claims, inv.clusters, inv.query)
        except Exception as exc:  # noqa: BLE001
            return self._unavailable(inv, duplication.DETECTOR_NAME, exc, started)
        inv.independence = dup.summary
        inv.evidence_spans.extend(dup.evidence_spans)
        inv.findings.extend(dup.findings)
        return DetectorRun(
            detector=duplication.DETECTOR_NAME,
            status=DetectorStatus.OK,
            message=f"{len(inv.sources)} source(s) form {dup.independent_groups} apparently independent "
            f"group(s); {len(dup.summary.relationships)} relationship(s), {len(dup.findings)} finding(s).",
            latency_ms=int((time.perf_counter() - started) * 1000),
            finding_count=len(dup.findings),
        )

    async def _run_search(
        self, inv: Investigation, request: SearchRequest, planned: PlannedSearch | None = None
    ) -> tuple[SearchRun, dict[str, Any] | None]:
        run_id = new_id("srch")
        provenance: dict[str, Any] = {}
        if planned is not None:
            provenance = {
                "parent_search_id": planned.parent_search_id,
                "question": planned.question,
                "triggered_by": planned.triggered_by,
                "planned_at": utc_now(),
            }
        try:
            response = await self.client.search(request)
        except SerpApiError as exc:
            if planned is not None and isinstance(exc, SerpApiFixtureNotFound):
                # mock mode: recorded as planned but not executed — no credits spent, not a failure
                inv.warnings.append(
                    f"Mock mode: no captured fixture for the {request.reason.value} follow-up; it was planned but not run."
                )
                return (
                    SearchRun(id=run_id, investigation_id=inv.id, request=request, status=SearchRunStatus.SKIPPED,
                              notes=["No captured fixture for this follow-up in mock mode."], **provenance),
                    None,
                )
            inv.errors.append(f"Search failed ({request.reason.value}): {exc}")
            return (
                SearchRun(
                    id=run_id,
                    investigation_id=inv.id,
                    request=request,
                    status=SearchRunStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    **provenance,
                ),
                None,
            )

        items = cap_ranked_items(parse_response(response.raw, request.engine), self.settings.max_results_per_search)
        results = build_results(run_id, response, items)
        inv.results.extend(results)
        if response.fixture_kind == "reconstructed":
            inv.warnings.append(
                "Search data is a RECONSTRUCTED fixture (rebuilt from earlier processed output), "
                "not a raw SerpApi response."
            )
        run = SearchRun(
            id=run_id,
            investigation_id=inv.id,
            request=request,
            status=SearchRunStatus.OK if results else SearchRunStatus.EMPTY,
            data_mode=response.data_mode,
            fixture_kind=response.fixture_kind,
            fetched_at=response.fetched_at,
            latency_ms=response.latency_ms,
            result_count=len(results),
            serpapi_search_id=response.serpapi_search_id,
            notes=response.notes,
            **provenance,
        )
        return run, response.raw
