"""Per-request bag passed through gateway stages. Not an HTTP model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from control_plane.schemas import ChatCompletionRequest, ErrorCategory


@dataclass
class RequestContext:
    request_id: str
    trace_id: str
    tenant_id: str
    api_key_id: str
    config_version: str
    logical_alias: str
    request: ChatCompletionRequest
    idempotency_key: str | None = None
    redacted_messages: list[dict[str, Any]] | None = None
    cache_eligible: bool = False
    cache_hit: bool = False
    retried: bool = False
    fallback_used: bool = False
    resolved_provider: str | None = None
    resolved_model: str | None = None
    error_category: ErrorCategory | None = None
    # Stage scratch; keep small — prefer typed fields over dumping arbitrary state.
    extras: dict[str, Any] = field(default_factory=dict)
