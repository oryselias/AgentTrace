"""M2a tenant controls self-check. Run: python -m tests.check_m2a_policies"""

from __future__ import annotations

from control_plane.cache import cache_eligible, exact_cache_key
from control_plane.gateway import Gateway
from control_plane.policies import (
    Tenant,
    hash_api_key,
    redact_text,
)
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
from control_plane.settings import load_tenants


def _route(**overrides: object) -> RouteConfig:
    base = dict(
        version="m2a-test",
        alias="support-default",
        primary=ModelEndpoint(provider="fake", model="fake-support", timeout_ms=100),
        fallback=ModelEndpoint(provider="fake", model="fake-support-fallback", timeout_ms=100),
        max_retries=1,
        circuit_failure_threshold=5,
        circuit_reset_ms=30_000,
    )
    base.update(overrides)
    return RouteConfig.model_validate(base)


def _tenant(*, api_key: str = "sk-test", **overrides: object) -> Tenant:
    base: dict[str, object] = dict(
        tenant_id="t1",
        key_id="k1",
        key_hash=hash_api_key(api_key),
        rpm=60,
        daily_budget_usd=10.0,
        cost_per_1k_tokens=0.002,
    )
    base.update(overrides)
    return Tenant(
        tenant_id=str(base["tenant_id"]),
        key_id=str(base["key_id"]),
        key_hash=str(base["key_hash"]),
        rpm=int(base["rpm"]),  # type: ignore[arg-type]
        daily_budget_usd=float(base["daily_budget_usd"]),  # type: ignore[arg-type]
        cost_per_1k_tokens=None if base["cost_per_1k_tokens"] is None else float(base["cost_per_1k_tokens"]),  # type: ignore[arg-type]
    )


def _req(content: str, **kwargs: object) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="support-default",
        messages=[ChatMessage(role="user", content=content)],
        **kwargs,  # type: ignore[arg-type]
    )


def _check_pii() -> None:
    text, hit = redact_text("email me at jane.doe@example.com or +1 (415) 555-0100 about ACCT-998877")
    assert hit is True
    assert "[EMAIL]" in text
    assert "[PHONE]" in text
    assert "[ACCOUNT]" in text
    assert "jane.doe" not in text


def _check_auth() -> None:
    gw = Gateway(_route(), tenants=[_tenant(api_key="sk-good")])
    denied = gw.complete_chat(_req("billing"), request_id="req_auth0", api_key=None)
    assert isinstance(denied, GatewayErrorBody)
    assert denied.error == ErrorCategory.PERMANENT_CLIENT_ERROR
    assert "api key" in denied.message

    bad = gw.complete_chat(_req("billing"), request_id="req_auth1", api_key="sk-wrong")
    assert isinstance(bad, GatewayErrorBody)
    assert bad.message == "invalid api key"

    ok = gw.complete_chat(_req("billing issue"), request_id="req_auth2", api_key="sk-good")
    assert isinstance(ok, ChatCompletionResponse)


def _check_pii_reaches_provider() -> None:
    gw = Gateway(_route(), tenants=[_tenant(api_key="sk-good")])
    result = gw.complete_chat(
        _req("billing for jane@corp.com ACCT-12345"),
        request_id="req_pii",
        api_key="sk-good",
    )
    assert isinstance(result, ChatCompletionResponse)
    fake = gw.provider("fake")
    assert isinstance(fake, FakeProvider)
    sent = fake.calls[-1].messages[-1]["content"]
    assert "jane@corp.com" not in sent
    assert "[EMAIL]" in sent
    assert "[ACCOUNT]" in sent


def _check_rpm() -> None:
    clock = {"t": 0.0}
    gw = Gateway(
        _route(),
        tenants=[_tenant(api_key="sk-good", rpm=2)],
        clock=lambda: clock["t"],
    )
    assert isinstance(gw.complete_chat(_req("billing a"), request_id="r1", api_key="sk-good"), ChatCompletionResponse)
    assert isinstance(gw.complete_chat(_req("billing b"), request_id="r2", api_key="sk-good"), ChatCompletionResponse)
    limited = gw.complete_chat(_req("billing c"), request_id="r3", api_key="sk-good")
    assert isinstance(limited, GatewayErrorBody)
    assert limited.error == ErrorCategory.RATE_LIMITED


def _check_budget() -> None:
    # Tiny budget: first success charges past it; second is denied.
    gw = Gateway(
        _route(),
        tenants=[_tenant(api_key="sk-good", daily_budget_usd=0.000001, cost_per_1k_tokens=1.0)],
    )
    first = gw.complete_chat(_req("billing once"), request_id="b1", api_key="sk-good")
    assert isinstance(first, ChatCompletionResponse)
    assert gw.budget.spent("t1") > 0
    second = gw.complete_chat(_req("billing twice"), request_id="b2", api_key="sk-good")
    assert isinstance(second, GatewayErrorBody)
    assert "budget" in second.message


def _check_idempotency_no_double_bill() -> None:
    gw = Gateway(_route(), tenants=[_tenant(api_key="sk-good")])
    r1 = gw.complete_chat(
        _req("billing idem"),
        request_id="i1",
        api_key="sk-good",
        idempotency_key="idem-1",
    )
    assert isinstance(r1, ChatCompletionResponse)
    spent_after = gw.budget.spent("t1")
    fake = gw.provider("fake")
    assert isinstance(fake, FakeProvider)
    calls_after = fake.call_count

    r2 = gw.complete_chat(
        _req("billing idem"),
        request_id="i2",
        api_key="sk-good",
        idempotency_key="idem-1",
    )
    assert isinstance(r2, ChatCompletionResponse)
    assert r2.choices[0].message.content == r1.choices[0].message.content
    assert gw.budget.spent("t1") == spent_after
    assert fake.call_count == calls_after


def _check_exact_cache() -> None:
    gw = Gateway(_route(), tenants=[_tenant(api_key="sk-good")])
    r1 = gw.complete_chat(_req("billing cache me"), request_id="c1", api_key="sk-good")
    assert isinstance(r1, ChatCompletionResponse)
    fake = gw.provider("fake")
    assert isinstance(fake, FakeProvider)
    calls = fake.call_count
    spent = gw.budget.spent("t1")

    r2 = gw.complete_chat(_req("billing cache me"), request_id="c2", api_key="sk-good")
    assert isinstance(r2, ChatCompletionResponse)
    assert r2.choices[0].message.content == r1.choices[0].message.content
    assert fake.call_count == calls  # cache hit
    assert gw.budget.spent("t1") == spent  # no double bill

    # temperature > 0 → not eligible
    assert cache_eligible(_req("x", temperature=0.7)) is False
    r3 = gw.complete_chat(
        _req("billing cache me", temperature=0.7),
        request_id="c3",
        api_key="sk-good",
    )
    assert isinstance(r3, ChatCompletionResponse)
    assert fake.call_count == calls + 1


def _check_cache_key_stable() -> None:
    msgs = [{"role": "user", "content": "hello"}]
    a = exact_cache_key(
        tenant_id="t1",
        config_version="v1",
        alias="support-default",
        messages=msgs,
        temperature=None,
        max_tokens=100,
        response_format=None,
    )
    b = exact_cache_key(
        tenant_id="t1",
        config_version="v1",
        alias="support-default",
        messages=msgs,
        temperature=None,
        max_tokens=100,
        response_format=None,
    )
    assert a == b
    c = exact_cache_key(
        tenant_id="t2",
        config_version="v1",
        alias="support-default",
        messages=msgs,
        temperature=None,
        max_tokens=100,
        response_format=None,
    )
    assert a != c


def _check_tenants_yaml() -> None:
    tenants = load_tenants()
    assert len(tenants) >= 1
    assert tenants[0].tenant_id == "tenant_demo"
    assert tenants[0].key_hash == hash_api_key("sk-demo-tenant-a")


def main() -> None:
    _check_pii()
    _check_auth()
    _check_pii_reaches_provider()
    _check_rpm()
    _check_budget()
    _check_idempotency_no_double_bill()
    _check_exact_cache()
    _check_cache_key_stable()
    _check_tenants_yaml()
    print("M2a policies OK")


if __name__ == "__main__":
    main()
