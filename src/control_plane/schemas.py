"""Frozen request/response and failure contracts. Change only with an intentional bump."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorCategory(StrEnum):
    """Stable internal failure classes (HANDOFF error handling rules)."""

    TIMEOUT = "timeout"
    RATE_LIMITED = "rate-limited"
    UNAVAILABLE = "unavailable"
    INVALID_RESPONSE = "invalid-response"
    PERMANENT_CLIENT_ERROR = "permanent-client-error"


# Transient → eligible for the single configured retry. Never retry permanent-client-error.
TRANSIENT_ERRORS: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMITED,
        ErrorCategory.UNAVAILABLE,
    }
)


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["system", "user", "assistant", "tool"]
    content: str


class ChatCompletionRequest(BaseModel):
    """Minimal OpenAI-compatible chat completion body. Logical alias in `model`."""

    model_config = ConfigDict(extra="ignore")

    model: str
    messages: list[ChatMessage] = Field(min_length=1)
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    response_format: dict[str, Any] | None = None


class ChatChoiceMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["assistant"] = "assistant"
    content: str


class ChatChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = 0
    message: ChatChoiceMessage
    finish_reason: str = "stop"


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class ChatCompletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    object: Literal["chat.completion"] = "chat.completion"
    model: str
    choices: list[ChatChoice] = Field(min_length=1)
    usage: Usage


class GatewayErrorBody(BaseModel):
    """Typed error returned when the gateway cannot satisfy the response contract."""

    model_config = ConfigDict(extra="forbid")

    error: ErrorCategory
    message: str
    request_id: str
    retryable: bool = False


class TraceRecord(BaseModel):
    """Persisted request metadata — never unredacted prompt contents."""

    model_config = ConfigDict(extra="forbid")

    request_id: str
    trace_id: str
    tenant_id: str
    logical_alias: str
    resolved_provider: str | None = None
    resolved_model: str | None = None
    config_version: str
    cache_hit: bool = False
    retried: bool = False
    fallback_used: bool = False
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    estimated_cost: float | None = None  # None = unknown; never invent
    provider_latency_ms: float | None = None
    end_to_end_latency_ms: float | None = None
    validation_ok: bool | None = None
    error_category: ErrorCategory | None = None
    redaction_applied: bool = False
    denied: bool = False


class ModelEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    timeout_ms: int = 5000


class RouteConfig(BaseModel):
    """YAML config surface shared by gateway load and eval gate."""

    model_config = ConfigDict(extra="forbid")

    version: str
    alias: str
    primary: ModelEndpoint
    fallback: ModelEndpoint | None = None
    max_retries: int = Field(default=1, ge=0, le=1)  # HANDOFF: at most one retry
    circuit_failure_threshold: int = Field(default=5, ge=1)
    circuit_reset_ms: int = Field(default=30_000, ge=1)
    max_prompt_tokens: int = Field(default=4096, ge=1)
    max_output_tokens: int = Field(default=1024, ge=1)
    response_schema: str | None = None  # optional path to JSON schema
