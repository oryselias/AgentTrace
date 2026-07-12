"""Request orchestration: auth → PII → limits → cache → primary/retry → fallback → trace."""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

from control_plane.cache import MemoryCache, cache_eligible, exact_cache_key
from control_plane.metrics import GatewayMetrics
from control_plane.policies import (
    BudgetTracker,
    IdempotencyStore,
    RateLimiter,
    Tenant,
    authenticate,
    estimate_cost_usd,
    redact_messages,
)
from control_plane.providers.base import Provider, ProviderFailure, ProviderRequest, ProviderResult, ProviderSuccess
from control_plane.providers.fake import FakeProvider
from control_plane.providers.openai_compatible import OpenAICompatibleProvider
from control_plane.routing import CircuitBreaker
from control_plane.schemas import (
    TRANSIENT_ERRORS,
    ChatChoice,
    ChatChoiceMessage,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorCategory,
    GatewayErrorBody,
    ModelEndpoint,
    RouteConfig,
    TraceRecord,
    Usage,
)
from control_plane.traces import MemoryTraceStore, TraceStore

# Fallback eligible after primary is exhausted. Permanent client errors never fall back.
_FALLBACK_ERRORS: frozenset[ErrorCategory] = TRANSIENT_ERRORS | {ErrorCategory.INVALID_RESPONSE}


def _provider_for(name: str) -> Provider:
    if name == "fake":
        return FakeProvider()
    if name == "openai":
        return OpenAICompatibleProvider.from_env()
    raise ValueError(f"unknown provider {name!r}")


def _to_response(body: ChatCompletionRequest, rid: str, result: ProviderSuccess) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id=f"chatcmpl_{rid.removeprefix('req_')}",
        model=body.model,
        choices=[
            ChatChoice(
                message=ChatChoiceMessage(content=result.content),
                finish_reason="stop",
            )
        ],
        usage=Usage(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.prompt_tokens + result.completion_tokens,
        ),
    )


def _to_error(rid: str, failure: ProviderFailure) -> GatewayErrorBody:
    return GatewayErrorBody(
        error=failure.category,
        message=failure.message,
        request_id=rid,
        retryable=failure.category in TRANSIENT_ERRORS,
    )


def _with_rid(err: GatewayErrorBody, rid: str) -> GatewayErrorBody:
    if err.request_id:
        return err
    return err.model_copy(update={"request_id": rid})


@dataclass
class _CallStats:
    result: ProviderResult
    retried: bool
    provider: str
    model: str


class Gateway:
    """Stateful runtime: shared providers, breakers, limits, cache, idempotency, traces."""

    def __init__(
        self,
        route: RouteConfig,
        *,
        tenants: list[Tenant] | None = None,
        clock: Callable[[], float] | None = None,
        cache: MemoryCache | None = None,
        traces: TraceStore | None = None,
        metrics: GatewayMetrics | None = None,
    ) -> None:
        self.route = route
        self._tenants_by_hash = {t.key_hash: t for t in (tenants or []) if t.key_hash}
        self._providers: dict[str, Provider] = {}
        self.cache = cache or MemoryCache()
        self.traces = traces or MemoryTraceStore()
        self.metrics = metrics or GatewayMetrics()
        self.rate_limiter = RateLimiter(clock=clock)
        self.budget = BudgetTracker()
        self.idempotency = IdempotencyStore()
        self.primary_breaker = CircuitBreaker(
            failure_threshold=route.circuit_failure_threshold,
            reset_ms=route.circuit_reset_ms,
            clock=clock,
        )
        self.fallback_breaker = (
            CircuitBreaker(
                failure_threshold=route.circuit_failure_threshold,
                reset_ms=route.circuit_reset_ms,
                clock=clock,
            )
            if route.fallback
            else None
        )

    def provider(self, name: str) -> Provider:
        if name not in self._providers:
            self._providers[name] = _provider_for(name)
        return self._providers[name]

    def complete_chat(
        self,
        body: ChatCompletionRequest,
        *,
        request_id: str | None = None,
        api_key: str | None = None,
        idempotency_key: str | None = None,
    ) -> ChatCompletionResponse | GatewayErrorBody:
        t0 = time.perf_counter()
        rid = request_id or f"req_{uuid.uuid4().hex[:12]}"
        tid = f"tr_{uuid.uuid4().hex[:12]}"

        auth = authenticate(api_key, self._tenants_by_hash)
        if isinstance(auth, GatewayErrorBody):
            err = _with_rid(auth, rid)
            self._emit(
                request_id=rid,
                trace_id=tid,
                tenant_id="unknown",
                alias=body.model,
                t0=t0,
                result=err,
                denied=True,
            )
            return err
        tenant = auth

        if idempotency_key:
            prior = self.idempotency.get(tenant.tenant_id, idempotency_key)
            if prior is not None:
                # Replay: no re-bill, still emit a trace for the client request_id.
                self._emit(
                    request_id=rid,
                    trace_id=tid,
                    tenant_id=tenant.tenant_id,
                    alias=body.model,
                    t0=t0,
                    result=prior,
                    cache_hit=isinstance(prior, ChatCompletionResponse),
                )
                return prior

        if body.stream:
            err = GatewayErrorBody(
                error=ErrorCategory.PERMANENT_CLIENT_ERROR,
                message="streaming is not supported",
                request_id=rid,
                retryable=False,
            )
            return self._finish(tenant, idempotency_key, err, charge=None, request_id=rid, trace_id=tid, alias=body.model, t0=t0, denied=True)

        if body.model != self.route.alias:
            err = GatewayErrorBody(
                error=ErrorCategory.PERMANENT_CLIENT_ERROR,
                message=f"unknown model alias {body.model!r}; expected {self.route.alias!r}",
                request_id=rid,
                retryable=False,
            )
            return self._finish(tenant, idempotency_key, err, charge=None, request_id=rid, trace_id=tid, alias=body.model, t0=t0, denied=True)

        if not self.rate_limiter.allow(tenant.tenant_id, tenant.rpm):
            err = GatewayErrorBody(
                error=ErrorCategory.RATE_LIMITED,
                message="tenant rpm exceeded",
                request_id=rid,
                retryable=True,
            )
            return self._finish(tenant, idempotency_key, err, charge=None, request_id=rid, trace_id=tid, alias=body.model, t0=t0, denied=True)

        if tenant.daily_budget_usd >= 0 and self.budget.spent(tenant.tenant_id) >= tenant.daily_budget_usd:
            err = GatewayErrorBody(
                error=ErrorCategory.PERMANENT_CLIENT_ERROR,
                message="tenant daily budget exceeded",
                request_id=rid,
                retryable=False,
            )
            return self._finish(tenant, idempotency_key, err, charge=None, request_id=rid, trace_id=tid, alias=body.model, t0=t0, denied=True)

        messages = [m.model_dump() for m in body.messages]
        messages, redacted = redact_messages(messages)
        max_tokens = body.max_tokens or self.route.max_output_tokens

        eligible = cache_eligible(body)
        cache_key: str | None = None
        if eligible:
            cache_key = exact_cache_key(
                tenant_id=tenant.tenant_id,
                config_version=self.route.version,
                alias=body.model,
                messages=messages,
                temperature=body.temperature,
                max_tokens=max_tokens,
                response_format=body.response_format,
            )
            hit = self.cache.get(cache_key)
            if hit is not None:
                cached = hit.model_copy(update={"id": f"chatcmpl_{rid.removeprefix('req_')}"})
                return self._finish(
                    tenant,
                    idempotency_key,
                    cached,
                    charge=None,
                    request_id=rid,
                    trace_id=tid,
                    alias=body.model,
                    t0=t0,
                    cache_hit=True,
                    redaction_applied=redacted,
                )

        primary = self._call_endpoint(
            self.route.primary,
            self.primary_breaker,
            messages=messages,
            max_tokens=max_tokens,
            temperature=body.temperature,
            response_format=body.response_format,
        )
        if isinstance(primary.result, ProviderSuccess):
            resp = _to_response(body, rid, primary.result)
            if cache_key is not None:
                self.cache.set(cache_key, resp)
            cost = estimate_cost_usd(
                primary.result.prompt_tokens,
                primary.result.completion_tokens,
                tenant.cost_per_1k_tokens,
            )
            return self._finish(
                tenant,
                idempotency_key,
                resp,
                charge=cost,
                request_id=rid,
                trace_id=tid,
                alias=body.model,
                t0=t0,
                redaction_applied=redacted,
                retried=primary.retried,
                resolved_provider=primary.provider,
                resolved_model=primary.model,
                provider_latency_ms=primary.result.latency_ms,
                prompt_tokens=primary.result.prompt_tokens,
                completion_tokens=primary.result.completion_tokens,
                estimated_cost=cost,
            )

        assert isinstance(primary.result, ProviderFailure)
        if primary.result.category == ErrorCategory.PERMANENT_CLIENT_ERROR:
            return self._finish(
                tenant,
                idempotency_key,
                _to_error(rid, primary.result),
                charge=None,
                request_id=rid,
                trace_id=tid,
                alias=body.model,
                t0=t0,
                redaction_applied=redacted,
                retried=primary.retried,
                resolved_provider=primary.provider,
                resolved_model=primary.model,
                provider_latency_ms=primary.result.latency_ms,
            )

        if (
            self.route.fallback is not None
            and self.fallback_breaker is not None
            and primary.result.category in _FALLBACK_ERRORS
        ):
            fb = self._call_endpoint(
                self.route.fallback,
                self.fallback_breaker,
                messages=messages,
                max_tokens=max_tokens,
                temperature=body.temperature,
                response_format=body.response_format,
            )
            if isinstance(fb.result, ProviderSuccess):
                resp = _to_response(body, rid, fb.result)
                if cache_key is not None:
                    self.cache.set(cache_key, resp)
                cost = estimate_cost_usd(
                    fb.result.prompt_tokens,
                    fb.result.completion_tokens,
                    tenant.cost_per_1k_tokens,
                )
                return self._finish(
                    tenant,
                    idempotency_key,
                    resp,
                    charge=cost,
                    request_id=rid,
                    trace_id=tid,
                    alias=body.model,
                    t0=t0,
                    redaction_applied=redacted,
                    retried=primary.retried or fb.retried,
                    fallback_used=True,
                    resolved_provider=fb.provider,
                    resolved_model=fb.model,
                    provider_latency_ms=fb.result.latency_ms,
                    prompt_tokens=fb.result.prompt_tokens,
                    completion_tokens=fb.result.completion_tokens,
                    estimated_cost=cost,
                )
            assert isinstance(fb.result, ProviderFailure)
            return self._finish(
                tenant,
                idempotency_key,
                _to_error(rid, fb.result),
                charge=None,
                request_id=rid,
                trace_id=tid,
                alias=body.model,
                t0=t0,
                redaction_applied=redacted,
                retried=primary.retried or fb.retried,
                fallback_used=True,
                resolved_provider=fb.provider,
                resolved_model=fb.model,
                provider_latency_ms=fb.result.latency_ms,
            )

        return self._finish(
            tenant,
            idempotency_key,
            _to_error(rid, primary.result),
            charge=None,
            request_id=rid,
            trace_id=tid,
            alias=body.model,
            t0=t0,
            redaction_applied=redacted,
            retried=primary.retried,
            resolved_provider=primary.provider,
            resolved_model=primary.model,
            provider_latency_ms=primary.result.latency_ms,
        )

    def _finish(
        self,
        tenant: Tenant,
        idempotency_key: str | None,
        result: ChatCompletionResponse | GatewayErrorBody,
        *,
        charge: float | None,
        request_id: str,
        trace_id: str,
        alias: str,
        t0: float,
        cache_hit: bool = False,
        redaction_applied: bool = False,
        retried: bool = False,
        fallback_used: bool = False,
        denied: bool = False,
        resolved_provider: str | None = None,
        resolved_model: str | None = None,
        provider_latency_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        estimated_cost: float | None = None,
    ) -> ChatCompletionResponse | GatewayErrorBody:
        if charge is not None and charge > 0:
            self.budget.charge(tenant.tenant_id, charge)
        if idempotency_key:
            self.idempotency.set(tenant.tenant_id, idempotency_key, result)
        self._emit(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant.tenant_id,
            alias=alias,
            t0=t0,
            result=result,
            cache_hit=cache_hit,
            redaction_applied=redaction_applied,
            retried=retried,
            fallback_used=fallback_used,
            denied=denied,
            resolved_provider=resolved_provider,
            resolved_model=resolved_model,
            provider_latency_ms=provider_latency_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            estimated_cost=estimated_cost if estimated_cost is not None else charge,
        )
        return result

    def _emit(
        self,
        *,
        request_id: str,
        trace_id: str,
        tenant_id: str,
        alias: str,
        t0: float,
        result: ChatCompletionResponse | GatewayErrorBody,
        cache_hit: bool = False,
        redaction_applied: bool = False,
        retried: bool = False,
        fallback_used: bool = False,
        denied: bool = False,
        resolved_provider: str | None = None,
        resolved_model: str | None = None,
        provider_latency_ms: float | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        estimated_cost: float | None = None,
    ) -> None:
        e2e = (time.perf_counter() - t0) * 1000.0
        error_cat: ErrorCategory | None = None
        pt, ct = prompt_tokens, completion_tokens
        if isinstance(result, GatewayErrorBody):
            error_cat = result.error
        elif isinstance(result, ChatCompletionResponse):
            pt = pt if pt is not None else result.usage.prompt_tokens
            ct = ct if ct is not None else result.usage.completion_tokens

        record = TraceRecord(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            logical_alias=alias,
            resolved_provider=resolved_provider,
            resolved_model=resolved_model,
            config_version=self.route.version,
            cache_hit=cache_hit,
            retried=retried,
            fallback_used=fallback_used,
            prompt_tokens=pt,
            completion_tokens=ct,
            estimated_cost=estimated_cost,
            provider_latency_ms=provider_latency_ms,
            end_to_end_latency_ms=e2e,
            validation_ok=None if error_cat else True,
            error_category=error_cat,
            redaction_applied=redaction_applied,
            denied=denied,
        )
        # Never attach prompt contents — TraceRecord has no message fields by design.
        self.traces.write(record)
        self.metrics.observe(
            cache_hit=cache_hit,
            retried=retried,
            fallback_used=fallback_used,
            denied=record.denied,
            redaction_applied=redaction_applied,
            error_category=error_cat.value if error_cat else None,
            prompt_tokens=pt,
            completion_tokens=ct,
            estimated_cost=estimated_cost,
            end_to_end_latency_ms=e2e,
        )

    def _call_endpoint(
        self,
        endpoint: ModelEndpoint,
        breaker: CircuitBreaker,
        *,
        messages: list[dict],
        max_tokens: int | None,
        temperature: float | None,
        response_format: dict | None,
    ) -> _CallStats:
        if not breaker.allow():
            return _CallStats(
                result=ProviderFailure(
                    category=ErrorCategory.UNAVAILABLE,
                    message="circuit open",
                    latency_ms=0.0,
                ),
                retried=False,
                provider=endpoint.provider,
                model=endpoint.model,
            )

        provider = self.provider(endpoint.provider)
        attempts = 1 + self.route.max_retries
        last: ProviderResult | None = None
        retried = False

        for attempt in range(attempts):
            last = provider.complete(
                ProviderRequest(
                    model=endpoint.model,
                    messages=messages,
                    timeout_ms=endpoint.timeout_ms,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    response_format=response_format,
                )
            )
            if isinstance(last, ProviderSuccess):
                breaker.record_success()
                return _CallStats(
                    result=last,
                    retried=retried,
                    provider=endpoint.provider,
                    model=endpoint.model,
                )

            assert isinstance(last, ProviderFailure)
            if last.category == ErrorCategory.PERMANENT_CLIENT_ERROR:
                return _CallStats(
                    result=last,
                    retried=retried,
                    provider=endpoint.provider,
                    model=endpoint.model,
                )

            breaker.record_failure()
            if last.category not in TRANSIENT_ERRORS or attempt + 1 >= attempts:
                return _CallStats(
                    result=last,
                    retried=retried,
                    provider=endpoint.provider,
                    model=endpoint.model,
                )
            retried = True

        assert last is not None
        return _CallStats(
            result=last,
            retried=retried,
            provider=endpoint.provider,
            model=endpoint.model,
        )


def complete_chat(
    body: ChatCompletionRequest,
    route: RouteConfig,
    *,
    request_id: str | None = None,
    api_key: str | None = None,
    idempotency_key: str | None = None,
) -> ChatCompletionResponse | GatewayErrorBody:
    return Gateway(route).complete_chat(
        body,
        request_id=request_id,
        api_key=api_key,
        idempotency_key=idempotency_key,
    )
