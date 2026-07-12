"""Prometheus text metrics — hand-rolled, no prometheus_client dep."""

from __future__ import annotations

from collections import defaultdict

_LATENCY_BUCKETS_MS = (5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000)


class GatewayMetrics:
    def __init__(self) -> None:
        self.requests_total = 0
        self.cache_hits_total = 0
        self.retries_total = 0
        self.fallbacks_total = 0
        self.denied_total = 0
        self.redactions_total = 0
        self.errors_total: dict[str, int] = defaultdict(int)
        self.prompt_tokens_total = 0
        self.completion_tokens_total = 0
        self.estimated_cost_total = 0.0
        self._latency_counts = [0] * len(_LATENCY_BUCKETS_MS)
        self._latency_inf = 0
        self._latency_sum = 0.0
        self._latency_count = 0

    def observe(
        self,
        *,
        cache_hit: bool = False,
        retried: bool = False,
        fallback_used: bool = False,
        denied: bool = False,
        redaction_applied: bool = False,
        error_category: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        estimated_cost: float | None = None,
        end_to_end_latency_ms: float | None = None,
    ) -> None:
        self.requests_total += 1
        if cache_hit:
            self.cache_hits_total += 1
        if retried:
            self.retries_total += 1
        if fallback_used:
            self.fallbacks_total += 1
        if denied:
            self.denied_total += 1
        if redaction_applied:
            self.redactions_total += 1
        if error_category:
            self.errors_total[error_category] += 1
        if prompt_tokens:
            self.prompt_tokens_total += prompt_tokens
        if completion_tokens:
            self.completion_tokens_total += completion_tokens
        if estimated_cost is not None:
            self.estimated_cost_total += estimated_cost
        if end_to_end_latency_ms is not None:
            self._observe_latency(end_to_end_latency_ms)

    def _observe_latency(self, ms: float) -> None:
        self._latency_sum += ms
        self._latency_count += 1
        placed = False
        for i, bound in enumerate(_LATENCY_BUCKETS_MS):
            if ms <= bound:
                self._latency_counts[i] += 1
                placed = True
                break
        if not placed:
            self._latency_inf += 1

    def render_prometheus(self) -> str:
        lines: list[str] = [
            "# HELP gateway_requests_total Total gateway requests",
            "# TYPE gateway_requests_total counter",
            f"gateway_requests_total {self.requests_total}",
            "# HELP gateway_cache_hits_total Exact cache hits",
            "# TYPE gateway_cache_hits_total counter",
            f"gateway_cache_hits_total {self.cache_hits_total}",
            "# HELP gateway_retries_total Requests that retried a provider call",
            "# TYPE gateway_retries_total counter",
            f"gateway_retries_total {self.retries_total}",
            "# HELP gateway_fallbacks_total Requests that used fallback",
            "# TYPE gateway_fallbacks_total counter",
            f"gateway_fallbacks_total {self.fallbacks_total}",
            "# HELP gateway_denied_total Denied requests (auth/limits)",
            "# TYPE gateway_denied_total counter",
            f"gateway_denied_total {self.denied_total}",
            "# HELP gateway_redactions_total Requests with PII redaction",
            "# TYPE gateway_redactions_total counter",
            f"gateway_redactions_total {self.redactions_total}",
            "# HELP gateway_prompt_tokens_total Prompt tokens",
            "# TYPE gateway_prompt_tokens_total counter",
            f"gateway_prompt_tokens_total {self.prompt_tokens_total}",
            "# HELP gateway_completion_tokens_total Completion tokens",
            "# TYPE gateway_completion_tokens_total counter",
            f"gateway_completion_tokens_total {self.completion_tokens_total}",
            "# HELP gateway_estimated_cost_usd_total Estimated spend (unknown costs omitted)",
            "# TYPE gateway_estimated_cost_usd_total counter",
            f"gateway_estimated_cost_usd_total {self.estimated_cost_total}",
            "# HELP gateway_errors_total Errors by category",
            "# TYPE gateway_errors_total counter",
        ]
        for cat, n in sorted(self.errors_total.items()):
            lines.append(f'gateway_errors_total{{category="{cat}"}} {n}')
        lines += [
            "# HELP gateway_request_latency_ms End-to-end latency",
            "# TYPE gateway_request_latency_ms histogram",
        ]
        cumulative = 0
        for bound, count in zip(_LATENCY_BUCKETS_MS, self._latency_counts, strict=True):
            cumulative += count
            lines.append(f'gateway_request_latency_ms_bucket{{le="{bound}"}} {cumulative}')
        cumulative += self._latency_inf
        lines.append(f'gateway_request_latency_ms_bucket{{le="+Inf"}} {cumulative}')
        lines.append(f"gateway_request_latency_ms_sum {self._latency_sum}")
        lines.append(f"gateway_request_latency_ms_count {self._latency_count}")
        return "\n".join(lines) + "\n"
