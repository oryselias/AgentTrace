"""Exact-cache store + eligibility. No semantic cache in v1."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Protocol, runtime_checkable

from control_plane.schemas import ChatCompletionRequest, ChatCompletionResponse


@runtime_checkable
class CacheStore(Protocol):
    def get(self, key: str) -> ChatCompletionResponse | None: ...

    def set(self, key: str, value: ChatCompletionResponse) -> None: ...


def cache_eligible(request: ChatCompletionRequest) -> bool:
    """Deterministic requests only: no stream, temperature unset or 0."""
    if request.stream:
        return False
    if request.temperature is not None and request.temperature != 0:
        return False
    return True


def exact_cache_key(
    *,
    tenant_id: str,
    config_version: str,
    alias: str,
    messages: list[dict[str, Any]],
    temperature: float | None,
    max_tokens: int | None,
    response_format: dict[str, Any] | None,
) -> str:
    payload = {
        "tenant_id": tenant_id,
        "config_version": config_version,
        "alias": alias,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "response_format": response_format,
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class MemoryCache:
    """Process-local exact cache. ponytail: upgrade to Redis when multi-worker."""

    def __init__(self) -> None:
        self._store: dict[str, ChatCompletionResponse] = {}

    def get(self, key: str) -> ChatCompletionResponse | None:
        return self._store.get(key)

    def set(self, key: str, value: ChatCompletionResponse) -> None:
        self._store[key] = value
