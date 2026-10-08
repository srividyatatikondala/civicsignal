"""Remove secrets from raw SerpApi payloads before they are cached, stored, or served."""

from __future__ import annotations

from typing import Any

_SECRET_KEYS = {"api_key", "apikey", "serp_api_key"}
_REDACTED = "[REDACTED]"


def scrub_secrets(obj: Any, secrets: list[str]) -> Any:
    if isinstance(obj, dict):
        return {
            k: (_REDACTED if k.lower() in _SECRET_KEYS else scrub_secrets(v, secrets))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [scrub_secrets(v, secrets) for v in obj]
    if isinstance(obj, str):
        for secret in secrets:
            if secret:
                obj = obj.replace(secret, _REDACTED)
        return obj
    return obj
