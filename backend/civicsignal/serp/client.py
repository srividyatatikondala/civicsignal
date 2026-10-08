"""
SerpApi client: the only module that talks to SerpApi.

Order of resolution for a request:
  1. mock mode   -> fixture store (never touches the network)
  2. cache hit   -> cached raw response (data_mode=cached)
  3. live call   -> with timeout + bounded retry on 429/5xx/timeouts/network

The API key is injected into the HTTP params at call time only. It is never
part of SearchRequest, cache keys, cached files, logs, or returned payloads.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import Settings
from ..logging_setup import log_event
from ..models import DataMode, SearchRequest, SerpApiResponse
from .cache import ResponseCache
from .errors import (
    SerpApiAuthError,
    SerpApiConfigError,
    SerpApiError,
    SerpApiFixtureNotFound,
    SerpApiQuotaError,
    SerpApiRateLimitError,
    SerpApiTimeoutError,
    SerpApiUpstreamError,
)
from .fixtures import FixtureStore
from .scrub import scrub_secrets

logger = logging.getLogger("civicsignal.serp.client")

# SerpApi reports "no results" as an error string with HTTP 200; that is an
# empty result set, not a failure.
_NO_RESULTS_MARKERS = ("hasn't returned any results", "has not returned any results", "no results")


def _classify_api_error(status: int, message: str) -> SerpApiError:
    msg = message.lower()
    if status in (401, 403) or "invalid api key" in msg:
        return SerpApiAuthError("SerpApi rejected the API key (invalid or unauthorized).")
    if "run out of searches" in msg:
        return SerpApiQuotaError("SerpApi account has no searches remaining.")
    if status == 429 or "throughput" in msg or "rate limit" in msg:
        return SerpApiRateLimitError("SerpApi rate limit reached.")
    if status >= 500:
        return SerpApiUpstreamError(f"SerpApi server error (HTTP {status}).", retryable=True)
    return SerpApiUpstreamError(f"SerpApi returned an error: {message[:200]}")


class SerpApiClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        cache: ResponseCache | None = None,
        fixtures: FixtureStore | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.settings = settings
        self._transport = transport
        self._sleep = sleep
        self.cache = cache if cache is not None else (
            ResponseCache(settings.cache_dir, settings.cache_ttl_hours) if settings.cache_enabled else None
        )
        self.fixtures = fixtures if fixtures is not None else FixtureStore(settings.fixtures_dir)
        self._secrets = settings.secret_values()

    @property
    def mock_mode(self) -> bool:
        return self.settings.mock_serpapi

    async def search(self, request: SearchRequest) -> SerpApiResponse:
        if self.mock_mode:
            return self._from_fixture(request)

        if not self.settings.serpapi_configured:
            raise SerpApiConfigError(
                "SERPAPI_API_KEY is not set. Set it in the environment or enable MOCK_SERPAPI=true."
            )

        key = request.cache_key()
        if self.cache:
            hit = self.cache.get(key)
            if hit:
                raw, fetched_at = hit
                log_event(logger, "serpapi_cache_hit", engine=request.engine, q=request.q)
                return SerpApiResponse(
                    request=request,
                    raw=raw,
                    data_mode=DataMode.CACHED,
                    fetched_at=fetched_at,
                    serpapi_search_id=_search_id(raw),
                )

        raw, latency_ms = await self._fetch_with_retry(request)
        fetched_at = datetime.now(timezone.utc)
        if self.cache:
            self.cache.set(key, raw, fetched_at, request.model_dump(mode="json"))
        return SerpApiResponse(
            request=request,
            raw=raw,
            data_mode=DataMode.LIVE,
            fetched_at=fetched_at,
            latency_ms=latency_ms,
            serpapi_search_id=_search_id(raw),
        )

    def _from_fixture(self, request: SearchRequest) -> SerpApiResponse:
        fixture = self.fixtures.find(request.engine, request.q, dict(request.params))
        if fixture is None:
            available = self.fixtures.available_queries()
            raise SerpApiFixtureNotFound(
                f"Mock mode: no fixture for engine={request.engine!r} q={request.q!r}. "
                f"Available fixture queries: {available}"
            )
        notes = [f"Served from fixture '{fixture.path.name}' ({fixture.kind})."]
        if fixture.note:
            notes.append(fixture.note)
        log_event(logger, "serpapi_fixture_hit", engine=request.engine, q=request.q, fixture=fixture.path.name)
        raw = scrub_secrets(fixture.response, self._secrets)
        return SerpApiResponse(
            request=request,
            raw=raw,
            data_mode=DataMode.MOCK,
            fetched_at=fixture.captured_at,
            serpapi_search_id=_search_id(raw),
            fixture_kind=fixture.kind,
            notes=notes,
        )

    async def _fetch_with_retry(self, request: SearchRequest) -> tuple[dict[str, Any], int]:
        attempts = self.settings.serpapi_max_retries + 1
        last_error: SerpApiError | None = None
        for attempt in range(1, attempts + 1):
            started = time.perf_counter()
            try:
                raw = await self._fetch_once(request)
                latency_ms = int((time.perf_counter() - started) * 1000)
                log_event(
                    logger,
                    "serpapi_call",
                    engine=request.engine,
                    q=request.q,
                    reason=request.reason.value,
                    attempt=attempt,
                    latency_ms=latency_ms,
                    organic_results=len(raw.get("organic_results") or []),
                )
                return raw, latency_ms
            except SerpApiError as exc:
                last_error = exc
                log_event(
                    logger,
                    "serpapi_call_failed",
                    logging.WARNING,
                    engine=request.engine,
                    q=request.q,
                    attempt=attempt,
                    error_type=type(exc).__name__,
                    error=str(exc),
                    will_retry=exc.retryable and attempt < attempts,
                )
                if not exc.retryable or attempt == attempts:
                    raise
                await self._sleep(self.settings.serpapi_backoff_s * (2 ** (attempt - 1)))
        assert last_error is not None
        raise last_error

    async def _fetch_once(self, request: SearchRequest) -> dict[str, Any]:
        params = {**request.to_query_params(), "output": "json"}
        params["api_key"] = self.settings.serpapi_api_key.get_secret_value()  # type: ignore[union-attr]
        try:
            async with httpx.AsyncClient(
                transport=self._transport, timeout=self.settings.serpapi_timeout_s
            ) as http:
                resp = await http.get(self.settings.serpapi_endpoint, params=params)
        except httpx.TimeoutException as exc:
            raise SerpApiTimeoutError(
                f"SerpApi did not respond within {self.settings.serpapi_timeout_s}s."
            ) from exc
        except httpx.RequestError as exc:
            # str(exc) can include the request URL (and therefore the key); don't echo it.
            raise SerpApiUpstreamError(
                f"Network error contacting SerpApi ({type(exc).__name__}).", retryable=True
            ) from None

        try:
            data = resp.json()
        except ValueError:
            data = None

        if not isinstance(data, dict):
            if resp.status_code >= 400:
                raise _classify_api_error(resp.status_code, resp.text[:200])
            raise SerpApiUpstreamError("SerpApi returned a non-JSON response.", retryable=True)

        data = scrub_secrets(data, self._secrets)
        error = data.get("error")
        if isinstance(error, str) and error:
            if resp.status_code < 400 and any(m in error.lower() for m in _NO_RESULTS_MARKERS):
                return data  # genuinely empty result set
            raise _classify_api_error(resp.status_code, error)
        if resp.status_code >= 400:
            raise _classify_api_error(resp.status_code, f"HTTP {resp.status_code}")
        return data


def _search_id(raw: dict[str, Any]) -> str | None:
    meta = raw.get("search_metadata") if isinstance(raw, dict) else None
    return meta.get("id") if isinstance(meta, dict) else None
