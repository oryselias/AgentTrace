"""M1b reliability self-check: retry, fallback, circuit. Run: python -m tests.check_m1b_reliability"""

from __future__ import annotations

import json

from control_plane.gateway import Gateway
from control_plane.providers.fake import FakeProvider
from control_plane.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ErrorCategory,
    GatewayErrorBody,
    ModelEndpoint,
    RouteConfig,
)


def _route(**overrides: object) -> RouteConfig:
    base = dict(
        version="m1b-test",
        alias="support-default",
        primary=ModelEndpoint(provider="fake", model="fake-support", timeout_ms=100),
        fallback=ModelEndpoint(provider="fake", model="fake-support-fallback", timeout_ms=100),
        max_retries=1,
        circuit_failure_threshold=5,
        circuit_reset_ms=30_000,
    )
    base.update(overrides)
    return RouteConfig.model_validate(base)


def _req(content: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="support-default",
        messages=[ChatMessage(role="user", content=content)],
    )


def _check_retry_then_ok() -> None:
    FakeProvider.reset_fail_once()
    gw = Gateway(_route())
    result = gw.complete_chat(_req("[[fake:fail-once:timeout]] billing issue"), request_id="req_retry")
    assert isinstance(result, ChatCompletionResponse)
    body = json.loads(result.choices[0].message.content)
    assert body["classification"] == "billing"
    fake = gw.provider("fake")
    assert isinstance(fake, FakeProvider)
    assert fake.call_count == 2  # fail + retry success on primary


def _check_no_retry_permanent() -> None:
    FakeProvider.reset_fail_once()
    gw = Gateway(_route())
    result = gw.complete_chat(
        _req("[[fake:permanent-client-error]] billing"),
        request_id="req_perm",
    )
    assert isinstance(result, GatewayErrorBody)
    assert result.error == ErrorCategory.PERMANENT_CLIENT_ERROR
    assert result.retryable is False
    fake = gw.provider("fake")
    assert isinstance(fake, FakeProvider)
    assert fake.call_count == 1  # no retry, no fallback


def _check_fallback_on_primary_model_failure() -> None:
    FakeProvider.reset_fail_once()
    gw = Gateway(_route())
    result = gw.complete_chat(
        _req("[[fake:unavailable:fake-support]] outage ticket"),
        request_id="req_fb",
    )
    assert isinstance(result, ChatCompletionResponse)
    body = json.loads(result.choices[0].message.content)
    assert body["classification"] == "outage"
    assert body["resolved_model"] == "fake-support-fallback"
    fake = gw.provider("fake")
    assert isinstance(fake, FakeProvider)
    # primary: attempt + retry, then fallback once
    assert fake.call_count == 3
    assert [c.model for c in fake.calls] == [
        "fake-support",
        "fake-support",
        "fake-support-fallback",
    ]


def _check_both_fail() -> None:
    FakeProvider.reset_fail_once()
    gw = Gateway(_route())
    result = gw.complete_chat(
        _req("[[fake:timeout]] still broken"),
        request_id="req_both",
    )
    assert isinstance(result, GatewayErrorBody)
    assert result.error == ErrorCategory.TIMEOUT
    assert result.retryable is True
    fake = gw.provider("fake")
    assert isinstance(fake, FakeProvider)
    # primary x2 + fallback x2
    assert fake.call_count == 4


def _check_circuit_opens_and_resets() -> None:
    FakeProvider.reset_fail_once()
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    gw = Gateway(
        _route(circuit_failure_threshold=2, circuit_reset_ms=1000, fallback=None),
        clock=now,
    )
    # Two failing requests (each: attempt + retry = 2 failures each → opens on 2nd failure of 1st req)
    # Actually each failed attempt records a failure. Req1: fail, fail → failures=2 → open.
    r1 = gw.complete_chat(_req("[[fake:unavailable]] x"), request_id="req_c1")
    assert isinstance(r1, GatewayErrorBody)
    assert gw.primary_breaker.opened_at is not None

    # Circuit open → no provider call
    before = gw.provider("fake")
    assert isinstance(before, FakeProvider)
    count_before = before.call_count
    r2 = gw.complete_chat(_req("[[fake:unavailable]] y"), request_id="req_c2")
    assert isinstance(r2, GatewayErrorBody)
    assert r2.error == ErrorCategory.UNAVAILABLE
    assert r2.message == "circuit open"
    assert before.call_count == count_before

    # Advance past reset → half-open probe allowed; fail-once then success path
    FakeProvider.reset_fail_once()
    clock["t"] += 1.5  # > 1000ms
    r3 = gw.complete_chat(
        _req("[[fake:fail-once:unavailable]] billing recover"),
        request_id="req_c3",
    )
    # half-open: attempt fails (fail-once), retry succeeds → circuit closes
    assert isinstance(r3, ChatCompletionResponse)
    assert gw.primary_breaker.opened_at is None
    assert gw.primary_breaker.failures == 0


def main() -> None:
    _check_retry_then_ok()
    _check_no_retry_permanent()
    _check_fallback_on_primary_model_failure()
    _check_both_fail()
    _check_circuit_opens_and_resets()
    print("M1b reliability OK")


if __name__ == "__main__":
    main()
