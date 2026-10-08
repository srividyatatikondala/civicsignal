"""
Optional live smoke test against the real SerpApi API. Spends one search.

    set SERPAPI_API_KEY in the environment, then:
    CIVICSIGNAL_LIVE_TESTS=1 .venv/Scripts/python -m pytest -m live
"""

import os

import pytest

from civicsignal.config import load_settings
from civicsignal.models import DataMode, SearchRequest
from civicsignal.serp import SerpApiClient, parse_response

pytestmark = [
    pytest.mark.live,
    pytest.mark.anyio,
    pytest.mark.skipif(
        os.environ.get("CIVICSIGNAL_LIVE_TESTS") != "1" or not os.environ.get("SERPAPI_API_KEY"),
        reason="live SerpApi tests disabled (set CIVICSIGNAL_LIVE_TESTS=1 and SERPAPI_API_KEY)",
    ),
]


async def test_live_google_search(tmp_path):
    settings = load_settings().model_copy(update={"mock_serpapi": False, "cache_dir": tmp_path})
    resp = await SerpApiClient(settings).search(
        SearchRequest(q="Aadhaar free update last date", params={"gl": "in", "hl": "en", "num": 10})
    )
    assert resp.data_mode == DataMode.LIVE
    assert parse_response(resp.raw, "google"), "expected at least one result"
