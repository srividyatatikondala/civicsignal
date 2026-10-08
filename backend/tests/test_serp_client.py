import json

import httpx
import pytest

from civicsignal.models import DataMode, SearchRequest
from civicsignal.serp import (
    FixtureStore,
    SerpApiAuthError,
    SerpApiClient,
    SerpApiConfigError,
    SerpApiFixtureNotFound,
    SerpApiQuotaError,
    SerpApiRateLimitError,
    SerpApiTimeoutError,
    SerpApiUpstreamError,
)

from .conftest import FAKE_KEY, sample_raw_response

pytestmark = pytest.mark.anyio

REQ = SearchRequest(q="aadhaar update deadline", params={"gl": "in", "hl": "en", "num": 10})


class Recorder:
    """httpx transport that replays a scripted sequence of responses/exceptions."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        status, body = step
        return httpx.Response(status, json=body) if isinstance(body, dict) else httpx.Response(status, text=body)


async def _no_sleep(_):
    return None


def _client(settings, recorder, **kw):
    return SerpApiClient(settings, transport=httpx.MockTransport(recorder), sleep=_no_sleep, **kw)


async def test_live_success_sends_key_and_returns_scrubbed_raw(tmp_settings):
    body = sample_raw_response()
    body["search_metadata"]["json_endpoint"] = f"https://serpapi.com/x?api_key={FAKE_KEY}"
    rec = Recorder((200, body))
    resp = await _client(tmp_settings, rec).search(REQ)
    assert resp.data_mode == DataMode.LIVE
    assert resp.serpapi_search_id == "abc123"
    sent = rec.requests[0].url.params
    assert sent["api_key"] == FAKE_KEY and sent["engine"] == "google" and sent["num"] == "10"
    assert FAKE_KEY not in json.dumps(resp.raw)


async def test_cache_hit_avoids_second_call_and_never_stores_key(tmp_settings):
    rec = Recorder((200, sample_raw_response()))
    client = _client(tmp_settings, rec)
    first = await client.search(REQ)
    second = await client.search(REQ)
    assert len(rec.requests) == 1
    assert second.data_mode == DataMode.CACHED
    assert second.fetched_at == first.fetched_at  # original retrieval time preserved
    for f in tmp_settings.cache_dir.glob("*.json"):
        assert FAKE_KEY not in f.read_text(encoding="utf-8")


async def test_cache_disabled(tmp_settings):
    settings = tmp_settings.model_copy(update={"cache_enabled": False})
    rec = Recorder((200, sample_raw_response()), (200, sample_raw_response()))
    client = _client(settings, rec)
    await client.search(REQ)
    await client.search(REQ)
    assert len(rec.requests) == 2


async def test_rate_limit_is_retried_then_succeeds(tmp_settings):
    rec = Recorder((429, {"error": "Your account has exceeded hourly throughput"}), (200, sample_raw_response()))
    resp = await _client(tmp_settings, rec).search(REQ)
    assert resp.data_mode == DataMode.LIVE
    assert len(rec.requests) == 2


async def test_server_error_retried_until_exhausted(tmp_settings):
    rec = Recorder((503, "unavailable"), (502, "bad gateway"), (500, "boom"))
    with pytest.raises(SerpApiUpstreamError):
        await _client(tmp_settings, rec).search(REQ)
    assert len(rec.requests) == 3  # 1 + max_retries(2)


async def test_invalid_key_is_not_retried(tmp_settings):
    rec = Recorder((401, {"error": "Invalid API key. Your API key should be here: ..."}))
    with pytest.raises(SerpApiAuthError):
        await _client(tmp_settings, rec).search(REQ)
    assert len(rec.requests) == 1


async def test_quota_exhausted_is_not_retried(tmp_settings):
    rec = Recorder((429, {"error": "Your account has run out of searches."}))
    with pytest.raises(SerpApiQuotaError):
        await _client(tmp_settings, rec).search(REQ)
    assert len(rec.requests) == 1


async def test_timeout_retried_then_raises(tmp_settings):
    rec = Recorder(*[httpx.ReadTimeout("slow")] * 3)
    with pytest.raises(SerpApiTimeoutError):
        await _client(tmp_settings, rec).search(REQ)
    assert len(rec.requests) == 3


async def test_network_error_message_does_not_leak_key(tmp_settings):
    rec = Recorder(*[httpx.ConnectError(f"failed for ?api_key={FAKE_KEY}")] * 3)
    with pytest.raises(SerpApiUpstreamError) as info:
        await _client(tmp_settings, rec).search(REQ)
    assert FAKE_KEY not in str(info.value)


async def test_no_results_is_empty_not_error(tmp_settings):
    rec = Recorder((200, {"search_metadata": {"id": "z"}, "error": "Google hasn't returned any results for this query."}))
    resp = await _client(tmp_settings, rec).search(REQ)
    assert resp.raw.get("organic_results") is None


async def test_non_json_response(tmp_settings):
    rec = Recorder(*[(200, "<html>oops</html>")] * 3)
    with pytest.raises(SerpApiUpstreamError):
        await _client(tmp_settings, rec).search(REQ)


async def test_missing_key_raises_config_error(tmp_settings):
    settings = tmp_settings.model_copy(update={"serpapi_api_key": None})
    with pytest.raises(SerpApiConfigError):
        await _client(settings, Recorder()).search(REQ)


async def test_mock_mode_serves_fixture_without_network(tmp_settings):
    settings = tmp_settings.model_copy(update={"mock_serpapi": True, "serpapi_api_key": None})
    store = FixtureStore(settings.fixtures_dir)
    store.save("f1", "google", "Aadhaar  Update Deadline", {"gl": "in"}, sample_raw_response(), kind="captured")
    rec = Recorder()
    resp = await _client(settings, rec, fixtures=store).search(REQ)
    assert rec.requests == []
    assert resp.data_mode == DataMode.MOCK
    assert resp.fixture_kind == "captured"
    assert any("f1.json" in n for n in resp.notes)


async def test_mock_mode_missing_fixture(tmp_settings):
    settings = tmp_settings.model_copy(update={"mock_serpapi": True})
    with pytest.raises(SerpApiFixtureNotFound):
        await _client(settings, Recorder()).search(REQ)


def test_repo_aadhaar_fixture_is_labeled_reconstructed(repo_fixtures_dir):
    f = FixtureStore(repo_fixtures_dir).find("google", "aadhaar free update last date 2026")
    assert f is not None
    assert f.kind == "reconstructed"
    assert "NOT a raw response" in f.note
