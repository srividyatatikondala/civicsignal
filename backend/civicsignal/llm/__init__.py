"""
Provider-agnostic LLM interface.

The deterministic pipeline never requires an LLM. When no provider is
configured (or a provider adapter is not implemented yet), get_llm_provider()
returns NullLLMProvider and callers skip LLM-assisted steps.

Adding a provider later = one class implementing LLMProvider, registered in
_ADAPTERS. No vendor SDK is installed until one is chosen.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from ..config import Settings
from ..logging_setup import log_event

logger = logging.getLogger("civicsignal.llm")


class LLMUnavailable(RuntimeError):
    pass


@runtime_checkable
class LLMProvider(Protocol):
    name: str

    @property
    def available(self) -> bool: ...

    async def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str: ...


class NullLLMProvider:
    name = "none"

    def __init__(self, reason: str = "No LLM provider configured."):
        self.reason = reason

    @property
    def available(self) -> bool:
        return False

    async def complete(self, prompt: str, *, system: str | None = None, max_tokens: int = 1024) -> str:
        raise LLMUnavailable(self.reason)


# provider name -> factory(settings) -> LLMProvider
_ADAPTERS: dict[str, object] = {}


def get_llm_provider(settings: Settings) -> LLMProvider:
    if not settings.llm_configured:
        return NullLLMProvider()
    name = (settings.llm_provider or "").strip().lower()
    factory = _ADAPTERS.get(name)
    if factory is None:
        log_event(logger, "llm_provider_not_implemented", logging.WARNING, provider=name)
        return NullLLMProvider(f"LLM provider '{name}' has no adapter yet; running rules-only.")
    return factory(settings)  # type: ignore[operator]
