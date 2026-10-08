"""
Runtime configuration, read from environment variables (and an optional
local .env file). Secrets are held as SecretStr so they never appear in
repr(), logs, or API responses.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import BaseModel, Field, SecretStr

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off", ""}


class ConfigError(ValueError):
    """Raised when an environment variable has an invalid value."""


class Settings(BaseModel):
    serpapi_api_key: SecretStr | None = None
    serpapi_endpoint: str = "https://serpapi.com/search.json"
    mock_serpapi: bool = False
    serpapi_timeout_s: float = Field(20.0, gt=0)
    serpapi_max_retries: int = Field(2, ge=0, le=5)
    serpapi_backoff_s: float = Field(1.0, ge=0)
    default_gl: str = "in"
    default_hl: str = "en"

    max_searches_per_investigation: int = Field(3, ge=1, le=10)
    max_results_per_search: int = Field(10, ge=1, le=100)
    planner_enabled: bool = True  # reason-coded follow-up searches
    max_follow_up_searches: int = Field(2, ge=0, le=5)

    cache_enabled: bool = True
    cache_ttl_hours: float = Field(24.0, ge=0)
    cache_dir: Path = PROJECT_ROOT / "data" / "cache" / "serpapi"
    fixtures_dir: Path = BACKEND_DIR / "fixtures" / "serpapi"
    database_path: Path = PROJECT_ROOT / "data" / "civicsignal.sqlite3"

    staleness_high_days: int = Field(60, ge=0)
    staleness_high_max_position: int = Field(5, ge=1)
    staleness_medium_days: int = Field(14, ge=0)

    llm_provider: str | None = None
    llm_api_key: SecretStr | None = None
    llm_model: str | None = None

    log_level: str = "INFO"

    @property
    def serpapi_configured(self) -> bool:
        return bool(self.serpapi_api_key and self.serpapi_api_key.get_secret_value())

    @property
    def llm_configured(self) -> bool:
        return bool(
            self.llm_provider and self.llm_api_key and self.llm_api_key.get_secret_value()
        )

    def secret_values(self) -> list[str]:
        """All secret strings, for log redaction and response scrubbing."""
        secrets = [self.serpapi_api_key, self.llm_api_key]
        return [s.get_secret_value() for s in secrets if s and s.get_secret_value()]


def _parse_bool(name: str, raw: str) -> bool:
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true/false, got {raw!r}")


def _path(raw: str) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else PROJECT_ROOT / p


# env var name -> (settings field, converter)
_ENV_MAP = {
    "SERPAPI_API_KEY": ("serpapi_api_key", str),
    "SERPAPI_ENDPOINT": ("serpapi_endpoint", str),
    "MOCK_SERPAPI": ("mock_serpapi", "bool"),
    "SERPAPI_TIMEOUT_SECONDS": ("serpapi_timeout_s", float),
    "SERPAPI_MAX_RETRIES": ("serpapi_max_retries", int),
    "SERPAPI_BACKOFF_SECONDS": ("serpapi_backoff_s", float),
    "SERPAPI_DEFAULT_GL": ("default_gl", str),
    "SERPAPI_DEFAULT_HL": ("default_hl", str),
    "MAX_SEARCHES_PER_INVESTIGATION": ("max_searches_per_investigation", int),
    "MAX_RESULTS_PER_SEARCH": ("max_results_per_search", int),
    "PLANNER_ENABLED": ("planner_enabled", "bool"),
    "MAX_FOLLOW_UP_SEARCHES": ("max_follow_up_searches", int),
    "SERPAPI_CACHE_ENABLED": ("cache_enabled", "bool"),
    "SERPAPI_CACHE_TTL_HOURS": ("cache_ttl_hours", float),
    "SERPAPI_CACHE_DIR": ("cache_dir", _path),
    "SERPAPI_FIXTURES_DIR": ("fixtures_dir", _path),
    "DATABASE_PATH": ("database_path", _path),
    "STALENESS_HIGH_DAYS": ("staleness_high_days", int),
    "STALENESS_HIGH_MAX_POSITION": ("staleness_high_max_position", int),
    "STALENESS_MEDIUM_DAYS": ("staleness_medium_days", int),
    "LLM_PROVIDER": ("llm_provider", str),
    "LLM_API_KEY": ("llm_api_key", str),
    "LLM_MODEL": ("llm_model", str),
    "LOG_LEVEL": ("log_level", str),
}


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """
    Build Settings from `env`. When `env` is None, uses the process
    environment layered over the project's .env file (process env wins).
    """
    if env is None:
        file_values = {
            k: v for k, v in dotenv_values(PROJECT_ROOT / ".env").items() if v is not None
        }
        env = {**file_values, **os.environ}

    kwargs: dict[str, object] = {}
    for env_name, (field, convert) in _ENV_MAP.items():
        raw = env.get(env_name)
        if raw is None or raw.strip() == "":
            continue
        try:
            if convert == "bool":
                kwargs[field] = _parse_bool(env_name, raw)
            else:
                kwargs[field] = convert(raw.strip())
        except ValueError as exc:
            raise ConfigError(f"Invalid value for {env_name}: {exc}") from exc
    return Settings(**kwargs)
