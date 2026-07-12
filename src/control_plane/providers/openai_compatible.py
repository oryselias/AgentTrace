"""Thin OpenAI-compatible chat/completions adapter. Fake remains CI authority."""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from control_plane.providers.base import ProviderFailure, ProviderRequest, ProviderResult, ProviderSuccess
from control_plane.schemas import ErrorCategory


def _classify_status(status: int) -> ErrorCategory:
    if status == 429:
        return ErrorCategory.RATE_LIMITED
    if status >= 500:
        return ErrorCategory.UNAVAILABLE
    # 4xx (incl. 401/403/400/404) — do not retry
    return ErrorCategory.PERMANENT_CLIENT_ERROR


def _safe_error_text(resp: httpx.Response, *, limit: int = 200) -> str:
    # Never echo credentials; truncate body so traces stay small.
    try:
        text = resp.text
    except Exception:
        return f"http {resp.status_code}"
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 3] + "..."
    return text or f"http {resp.status_code}"


class OpenAICompatibleProvider:
    """POST {base_url}/chat/completions. Maps upstream failures to ErrorCategory."""

    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        client: httpx.Client | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("api_key required for openai provider")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self._owns_client = client is None
        self._client = client or httpx.Client()

    @classmethod
    def from_env(cls) -> OpenAICompatibleProvider:
        key = os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ValueError("OPENAI_API_KEY required for openai provider")
        base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1"
        return cls(api_key=key, base_url=base)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def complete(self, request: ProviderRequest) -> ProviderResult:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
            "stream": False,
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_format is not None:
            payload["response_format"] = request.response_format

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(request.timeout_ms / 1000.0)
        started = time.perf_counter()
        try:
            resp = self._client.post(url, json=payload, headers=headers, timeout=timeout)
        except httpx.TimeoutException:
            return ProviderFailure(
                category=ErrorCategory.TIMEOUT,
                message="provider timeout",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )
        except httpx.RequestError as exc:
            return ProviderFailure(
                category=ErrorCategory.UNAVAILABLE,
                message=f"provider unreachable: {exc.__class__.__name__}",
                latency_ms=(time.perf_counter() - started) * 1000.0,
            )

        latency_ms = (time.perf_counter() - started) * 1000.0
        if resp.status_code != 200:
            return ProviderFailure(
                category=_classify_status(resp.status_code),
                message=_safe_error_text(resp),
                latency_ms=latency_ms,
            )

        try:
            data = resp.json()
        except ValueError:
            return ProviderFailure(
                category=ErrorCategory.INVALID_RESPONSE,
                message="provider returned non-json body",
                latency_ms=latency_ms,
            )

        return _parse_success(data, latency_ms=latency_ms, fallback_model=request.model)


def _parse_success(data: Any, *, latency_ms: float, fallback_model: str) -> ProviderResult:
    if not isinstance(data, dict):
        return ProviderFailure(
            category=ErrorCategory.INVALID_RESPONSE,
            message="provider json root is not an object",
            latency_ms=latency_ms,
        )
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        return ProviderFailure(
            category=ErrorCategory.INVALID_RESPONSE,
            message="provider response missing choices",
            latency_ms=latency_ms,
        )
    first = choices[0]
    if not isinstance(first, dict):
        return ProviderFailure(
            category=ErrorCategory.INVALID_RESPONSE,
            message="provider choice is not an object",
            latency_ms=latency_ms,
        )
    message = first.get("message")
    if not isinstance(message, dict):
        return ProviderFailure(
            category=ErrorCategory.INVALID_RESPONSE,
            message="provider choice missing message",
            latency_ms=latency_ms,
        )
    content = message.get("content")
    if not isinstance(content, str):
        return ProviderFailure(
            category=ErrorCategory.INVALID_RESPONSE,
            message="provider message content missing or not a string",
            latency_ms=latency_ms,
        )

    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    raw_model = data.get("model")
    if not isinstance(raw_model, str) or not raw_model:
        raw_model = fallback_model

    return ProviderSuccess(
        content=content,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        latency_ms=latency_ms,
        raw_model=raw_model,
    )
