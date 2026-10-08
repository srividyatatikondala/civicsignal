"""
Structured (JSON-lines) logging with secret redaction.

Use `log_event(logger, "event_name", key=value, ...)` so every log line is
machine-readable: investigation id, searches, latency, errors, etc.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

_REDACTED = "[REDACTED]"


class SecretRedactionFilter(logging.Filter):
    """Replaces any configured secret value appearing in a log record."""

    def __init__(self, secrets: list[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def _redact(self, value):
        if isinstance(value, str):
            for secret in self._secrets:
                value = value.replace(secret, _REDACTED)
            return value
        if isinstance(value, dict):
            return {k: self._redact(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return type(value)(self._redact(v) for v in value)
        return value

    def filter(self, record: logging.LogRecord) -> bool:
        if self._secrets:
            record.msg = self._redact(str(record.msg))
            if record.args:
                record.args = self._redact(record.args)
            fields = getattr(record, "fields", None)
            if fields:
                record.fields = self._redact(fields)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        payload.update(getattr(record, "fields", {}) or {})
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, ensure_ascii=False)


def configure_logging(level: str = "INFO", secrets: list[str] | None = None) -> None:
    root = logging.getLogger("civicsignal")
    root.setLevel(level.upper())
    root.propagate = False
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactionFilter(secrets or []))
    root.addHandler(handler)


def log_event(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})
