from __future__ import annotations

from pathlib import Path

import pytest

from civicsignal.config import BACKEND_DIR, Settings

FAKE_KEY = "test-key-0123456789abcdef"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def tmp_settings(tmp_path: Path) -> Settings:
    """Isolated settings: temp DB/cache/fixtures, a fake key, no backoff delay."""
    return Settings(
        serpapi_api_key=FAKE_KEY,
        serpapi_backoff_s=0,
        serpapi_max_retries=2,
        cache_dir=tmp_path / "cache",
        fixtures_dir=tmp_path / "fixtures",
        database_path=tmp_path / "test.sqlite3",
    )


@pytest.fixture
def repo_fixtures_dir() -> Path:
    """Test-only fixtures (incl. the reconstructed Aadhaar run). Real captures live in backend/fixtures/serpapi."""
    return BACKEND_DIR / "tests" / "fixtures" / "serpapi"


def sample_raw_response() -> dict:
    """SerpApi-shaped google response exercising the normalizer's edge cases."""
    return {
        "search_metadata": {"id": "abc123", "status": "Success"},
        "search_parameters": {"engine": "google", "q": "aadhaar update deadline"},
        "answer_box": {
            "title": "Free Aadhaar update",
            "link": "https://www.example.gov.in/aadhaar?utm_source=google",
            "snippet": "Free online document update is available till 14 June 2026.",
        },
        "organic_results": [
            {
                "position": 1,
                "title": "Free Aadhaar update",
                "link": "https://example.gov.in/aadhaar/",
                "snippet": "Free online document update is available till 14 June 2026.",
                "date": "Mar 3, 2026",
            },
            {
                "position": 2,
                "title": "Aadhaar update video",
                "link": "https://www.youtube.com/shorts/nzgTSu7pNTw",
                "snippet": "deadline June 14 2026",
            },
            {
                "position": 3,
                "title": "Same video, watch URL",
                "link": "https://m.youtube.com/watch?v=nzgTSu7pNTw&feature=share",
                "snippet": "deadline June 14 2026",
            },
            {
                "position": 4,
                "title": "Another video on the same domain",
                "link": "https://www.youtube.com/watch?v=wtnafTFIJzI",
                "snippet": "Last Date June 14, 2026",
            },
            {"position": 5, "title": "No link result", "snippet": "orphan snippet"},
            {"position": "6", "title": None, "link": "https://news.example.co.in/a#:~:text=till"},
            "not-a-dict",
        ],
        "related_questions": [
            {"question": "What is the last date?", "snippet": "June 14, 2026", "link": "https://blog.example.com/q"}
        ],
    }


def base_fixture_queries(fixtures_dir: Path) -> list[str]:
    """Queries of the captured BASE searches. Follow-up captures (note "Follow-up (...)") are excluded."""
    import json

    queries = []
    for path in sorted(Path(fixtures_dir).glob("*.json")):
        meta = json.loads(path.read_text(encoding="utf-8"))["civicsignal_fixture"]
        if meta["kind"] == "captured" and not meta.get("note", "").startswith("Follow-up ("):
            queries.append(meta["q"])
    return queries
