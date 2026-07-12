"""Deterministic fake provider — CI authority. Real adapters must match this contract."""

from __future__ import annotations

import json
import re
from typing import Any, ClassVar

from control_plane.providers.base import ProviderFailure, ProviderRequest, ProviderResult, ProviderSuccess
from control_plane.schemas import ErrorCategory

# [[fake:timeout]]
# [[fake:timeout:fake-support]]          — only that model
# [[fake:fail-once:timeout]]             — first call for (model, text) fails, then ok
# [[fake:fail-once:timeout:fake-support]]
_INJECT = re.compile(
    r"\[\[fake:(fail-once:)?"
    r"(timeout|rate-limited|unavailable|invalid-response|permanent-client-error)"
    r"(?::([^\]]+))?\]\]"
)

_CATEGORY = {
    "timeout": ErrorCategory.TIMEOUT,
    "rate-limited": ErrorCategory.RATE_LIMITED,
    "unavailable": ErrorCategory.UNAVAILABLE,
    "invalid-response": ErrorCategory.INVALID_RESPONSE,
    "permanent-client-error": ErrorCategory.PERMANENT_CLIENT_ERROR,
}


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return str(msg.get("content") or "")
    return ""


def _classify(text: str) -> str:
    lower = text.lower()
    for label in ("billing", "access", "outage", "refund", "security"):
        if label in lower:
            return label
    return "general"


def _tokens(text: str) -> int:
    # ponytail: ceiling = whitespace split; upgrade to tiktoken when cost demos need it
    return max(1, len(text.split()))


class FakeProvider:
    """Always returns ProviderResult; never raises for mapped failure classes."""

    name = "fake"
    _fail_once_fired: ClassVar[set[str]] = set()

    def __init__(self) -> None:
        self.call_count = 0
        self.calls: list[ProviderRequest] = []

    @classmethod
    def reset_fail_once(cls) -> None:
        cls._fail_once_fired.clear()

    def complete(self, request: ProviderRequest) -> ProviderResult:
        self.call_count += 1
        self.calls.append(request)
        text = _last_user_text(request.messages)
        inject = _INJECT.search(text)
        if inject:
            fail_once = bool(inject.group(1))
            cat = _CATEGORY[inject.group(2)]
            only_model = inject.group(3)
            if only_model is None or only_model == request.model:
                if fail_once:
                    key = f"{request.model}\0{text}"
                    if key not in self._fail_once_fired:
                        self._fail_once_fired.add(key)
                        return ProviderFailure(
                            category=cat,
                            message=f"fake injected {cat.value} (fail-once)",
                            latency_ms=1.0,
                        )
                    # already fired → fall through to success
                else:
                    return ProviderFailure(
                        category=cat,
                        message=f"fake injected {cat.value}",
                        latency_ms=1.0,
                    )

        # Deliberately bad candidate model for eval gate demos / CI.
        degraded = request.model.endswith("-degraded") or request.model == "fake-support-degraded"
        if degraded:
            body = {
                "classification": "general",
                "citation_ids": [],
                "proposal": "Unverified reply with no policy grounding. " * 40,
                "needs_human_review": True,
                "resolved_model": request.model,
            }
            content = json.dumps(body, separators=(",", ":"))
            prompt_tokens = _tokens(" ".join(str(m.get("content") or "") for m in request.messages))
            completion_tokens = _tokens(content)
            return ProviderSuccess(
                content=content,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                latency_ms=50.0,
                raw_model=request.model,
            )

        classification = _classify(text)
        body = {
            "classification": classification,
            "citation_ids": [f"pol-{classification}"],
            "proposal": f"Review policy pol-{classification} and propose a resolution.",
            "needs_human_review": True,
            "resolved_model": request.model,
        }
        content = json.dumps(body, separators=(",", ":"))
        prompt_tokens = _tokens(" ".join(str(m.get("content") or "") for m in request.messages))
        completion_tokens = _tokens(content)
        if request.max_tokens is not None:
            max_chars = max(1, request.max_tokens * 4)
            content = content[:max_chars]
            completion_tokens = min(completion_tokens, request.max_tokens)

        return ProviderSuccess(
            content=content,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=1.0,
            raw_model=request.model,
        )
