"""Auth, PII redaction, RPM, daily budget, idempotency. In-memory for M2a."""

from __future__ import annotations

import hashlib
import re
import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from control_plane.schemas import ChatCompletionResponse, ErrorCategory, GatewayErrorBody

# ponytail: regex PII has known misses/false positives; upgrade to a detector service if needed
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(
    r"\b(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
)
_DEFAULT_ACCOUNT = re.compile(r"\bACCT-\d{4,}\b")


def hash_api_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Tenant:
    tenant_id: str
    key_id: str
    key_hash: str
    rpm: int
    daily_budget_usd: float
    cost_per_1k_tokens: float | None  # None → cost unknown; never invent


def redact_text(text: str, *, account_re: re.Pattern[str] | None = None) -> tuple[str, bool]:
    """Return (redacted, applied)."""
    acct = account_re or _DEFAULT_ACCOUNT
    out, n = _EMAIL.subn("[EMAIL]", text)
    out, n2 = _PHONE.subn("[PHONE]", out)
    out, n3 = acct.subn("[ACCOUNT]", out)
    return out, (n + n2 + n3) > 0


def redact_messages(
    messages: list[dict[str, Any]],
    *,
    account_re: re.Pattern[str] | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    applied = False
    out: list[dict[str, Any]] = []
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            redacted, hit = redact_text(content, account_re=account_re)
            applied = applied or hit
            out.append({**msg, "content": redacted})
        else:
            out.append(dict(msg))
    return out, applied


def estimate_cost_usd(
    prompt_tokens: int,
    completion_tokens: int,
    cost_per_1k: float | None,
) -> float | None:
    if cost_per_1k is None:
        return None
    return ((prompt_tokens + completion_tokens) / 1000.0) * cost_per_1k


class RateLimiter:
    """Fixed 60s sliding window per tenant. ponytail: process-local; upgrade to Redis for multi-worker."""

    def __init__(self, *, clock: Callable[[], float] | None = None) -> None:
        self._clock = clock or time.monotonic
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, tenant_id: str, rpm: int) -> bool:
        now = self._clock()
        window = self._hits[tenant_id]
        while window and now - window[0] >= 60.0:
            window.popleft()
        if len(window) >= rpm:
            return False
        window.append(now)
        return True


class BudgetTracker:
    """Daily spend counter. ponytail: no midnight rollover yet; reset() for tests / later cron."""

    def __init__(self) -> None:
        self._spent: dict[str, float] = defaultdict(float)

    def spent(self, tenant_id: str) -> float:
        return self._spent[tenant_id]

    def would_exceed(self, tenant_id: str, budget: float, cost: float) -> bool:
        return self._spent[tenant_id] + cost > budget

    def charge(self, tenant_id: str, cost: float) -> None:
        self._spent[tenant_id] += cost

    def reset(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._spent.clear()
        else:
            self._spent.pop(tenant_id, None)


class IdempotencyStore:
    """Replay prior response for (tenant, key). ponytail: in-memory; upgrade to Redis+TTL."""

    def __init__(self) -> None:
        self._store: dict[tuple[str, str], ChatCompletionResponse | GatewayErrorBody] = {}

    def get(self, tenant_id: str, key: str) -> ChatCompletionResponse | GatewayErrorBody | None:
        return self._store.get((tenant_id, key))

    def set(
        self,
        tenant_id: str,
        key: str,
        value: ChatCompletionResponse | GatewayErrorBody,
    ) -> None:
        self._store[(tenant_id, key)] = value


def authenticate(
    raw_key: str | None,
    tenants_by_hash: dict[str, Tenant],
) -> Tenant | GatewayErrorBody:
    """Empty tenant map → anonymous (M1 tests). Non-empty → require valid key."""
    if not tenants_by_hash:
        return Tenant(
            tenant_id="anonymous",
            key_id="anon",
            key_hash="",
            rpm=10_000,
            daily_budget_usd=1_000_000.0,
            cost_per_1k_tokens=0.002,
        )
    if not raw_key:
        return GatewayErrorBody(
            error=ErrorCategory.PERMANENT_CLIENT_ERROR,
            message="missing api key",
            request_id="",  # filled by caller
            retryable=False,
        )
    tenant = tenants_by_hash.get(hash_api_key(raw_key))
    if tenant is None:
        return GatewayErrorBody(
            error=ErrorCategory.PERMANENT_CLIENT_ERROR,
            message="invalid api key",
            request_id="",
            retryable=False,
        )
    return tenant
