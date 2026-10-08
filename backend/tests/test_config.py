import logging

import pytest

from civicsignal.config import ConfigError, load_settings
from civicsignal.logging_setup import JsonFormatter, SecretRedactionFilter


def test_defaults_without_env():
    s = load_settings({})
    assert s.serpapi_configured is False
    assert s.mock_serpapi is False
    assert s.max_searches_per_investigation == 3
    assert s.max_results_per_search == 10
    assert s.llm_configured is False


def test_env_parsing():
    s = load_settings(
        {
            "SERPAPI_API_KEY": "abc",
            "MOCK_SERPAPI": "TRUE",
            "MAX_RESULTS_PER_SEARCH": "5",
            "SERPAPI_CACHE_ENABLED": "no",
            "LLM_PROVIDER": "someprovider",
        }
    )
    assert s.serpapi_configured and s.mock_serpapi
    assert s.max_results_per_search == 5
    assert s.cache_enabled is False
    assert s.llm_configured is False  # provider without key is not configured


def test_invalid_bool_raises():
    with pytest.raises(ConfigError):
        load_settings({"MOCK_SERPAPI": "maybe"})


def test_secret_never_in_repr_or_dump():
    s = load_settings({"SERPAPI_API_KEY": "super-secret-value"})
    assert "super-secret-value" not in repr(s)
    assert "super-secret-value" not in s.model_dump_json()


def test_log_redaction():
    record = logging.LogRecord("civicsignal", logging.INFO, __file__, 1, "url ?api_key=sk-123", None, None)
    record.fields = {"detail": "key sk-123 leaked", "nested": ["sk-123"]}
    SecretRedactionFilter(["sk-123"]).filter(record)
    line = JsonFormatter().format(record)
    assert "sk-123" not in line
    assert "[REDACTED]" in line
