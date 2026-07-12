"""Provider adapter contract. Fake is CI authority; real adapters must conform."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from control_plane.schemas import ErrorCategory


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    messages: list[dict[str, Any]]
    timeout_ms: int
    max_tokens: int | None = None
    temperature: float | None = None
    response_format: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProviderSuccess:
    content: str
    prompt_tokens: int
    completion_tokens: int
    latency_ms: float
    raw_model: str


@dataclass(frozen=True)
class ProviderFailure:
    category: ErrorCategory
    message: str
    latency_ms: float = 0.0


ProviderResult = ProviderSuccess | ProviderFailure


@runtime_checkable
class Provider(Protocol):
    name: str

    def complete(self, request: ProviderRequest) -> ProviderResult:
        """Call the upstream model. Must not raise for mapped failure classes."""
        ...
