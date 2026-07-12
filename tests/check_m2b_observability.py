"""M2b observability self-check. Run: python -m tests.check_m2b_observability"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from control_plane.api import app, gateway
from control_plane.gateway import Gateway
from control_plane.metrics import GatewayMetrics
from control_plane.policies import Tenant, hash_api_key
from control_plane.providers.fake import FakeProvider
from control_plane.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    ErrorCategory,
    GatewayErrorBody,
    ModelEndpoint,
    RouteConfig,
    TraceRecord,
)
from control_plane.traces import MemoryTraceStore, SqliteTraceStore


def _route(**overrides: object) -> RouteConfig:
    base = dict(
        version="m2b-test",
        alias="support-default",
        primary=ModelEndpoint(provider="fake", model="fake-support", timeout_ms=100),
        fallback=ModelEndpoint(provider="fake", model="fake-support-fallback", timeout_ms=100),
        max_retries=1,
        circuit_failure_threshold=5,
        circuit_reset_ms=30_000,
    )
    base.update(overrides)
    return RouteConfig.model_validate(base)


def _tenant(api_key: str = "sk-obs") -> Tenant:
    return Tenant(
        tenant_id="t_obs",
        key_id="k_obs",
        key_hash=hash_api_key(api_key),
        rpm=100,
        daily_budget_usd=10.0,
        cost_per_1k_tokens=0.002,
    )


def _req(content: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="support-default",
        messages=[ChatMessage(role="user", content=content)],
    )


def _assert_no_prompt_leak(record: TraceRecord) -> None:
    blob = record.model_dump_json()
    assert "messages" not in blob
    assert "content" not in blob
    assert "@" not in blob or record.redaction_applied  # email must not appear raw


def _check_trace_happy_and_injection() -> None:
    FakeProvider.reset_fail_once()
    store = MemoryTraceStore()
    metrics = GatewayMetrics()
    gw = Gateway(_route(), tenants=[_tenant()], traces=store, metrics=metrics)

    ok = gw.complete_chat(
        _req("billing for secret@example.com"),
        request_id="req_ok",
        api_key="sk-obs",
    )
    assert isinstance(ok, ChatCompletionResponse)
    rows = store.list_recent(limit=5)
    assert len(rows) == 1
    tr = rows[0]
    assert tr.request_id == "req_ok"
    assert tr.tenant_id == "t_obs"
    assert tr.config_version == "m2b-test"
    assert tr.redaction_applied is True
    assert tr.cache_hit is False
    assert tr.error_category is None
    assert tr.denied is False
    assert tr.resolved_provider == "fake"
    _assert_no_prompt_leak(tr)
    assert "secret@example.com" not in tr.model_dump_json()

    # Failure injection → traced error category
    fail = gw.complete_chat(
        _req("[[fake:timeout]] still broken"),
        request_id="req_fail",
        api_key="sk-obs",
    )
    assert isinstance(fail, GatewayErrorBody)
    assert fail.error == ErrorCategory.TIMEOUT
    tr2 = store.list_recent(limit=1)[0]
    assert tr2.request_id == "req_fail"
    assert tr2.error_category == ErrorCategory.TIMEOUT
    assert tr2.denied is False
    _assert_no_prompt_leak(tr2)

    # Fallback path traced
    FakeProvider.reset_fail_once()
    fb = gw.complete_chat(
        _req("[[fake:unavailable:fake-support]] outage ticket"),
        request_id="req_fb",
        api_key="sk-obs",
    )
    assert isinstance(fb, ChatCompletionResponse)
    tr3 = store.list_recent(limit=1)[0]
    assert tr3.fallback_used is True
    assert tr3.resolved_model == "fake-support-fallback"

    assert metrics.requests_total >= 3
    assert metrics.fallbacks_total >= 1
    assert metrics.errors_total.get("timeout", 0) >= 1
    assert metrics.redactions_total >= 1
    text = metrics.render_prometheus()
    assert "gateway_requests_total" in text
    assert "gateway_fallbacks_total" in text
    assert 'gateway_errors_total{category="timeout"}' in text


def _check_cache_hit_traced() -> None:
    store = MemoryTraceStore()
    gw = Gateway(_route(), tenants=[_tenant()], traces=store)
    r1 = gw.complete_chat(_req("billing cache obs"), request_id="c1", api_key="sk-obs")
    r2 = gw.complete_chat(_req("billing cache obs"), request_id="c2", api_key="sk-obs")
    assert isinstance(r1, ChatCompletionResponse)
    assert isinstance(r2, ChatCompletionResponse)
    hit = store.list_recent(limit=1)[0]
    assert hit.cache_hit is True
    assert hit.request_id == "c2"


def _check_sqlite_store() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "traces.db"
        store = SqliteTraceStore(path)
        try:
            store.write(
                TraceRecord(
                    request_id="req_sql",
                    trace_id="tr_sql",
                    tenant_id="t1",
                    logical_alias="support-default",
                    config_version="m2b",
                )
            )
            rows = store.list_recent(limit=10)
            assert len(rows) == 1
            assert rows[0].request_id == "req_sql"
        finally:
            store.close()


def _check_http_metrics_and_traces() -> None:
    # Use the module gateway (shared); just hit endpoints after a chat call.
    client = TestClient(app)
    headers = {"Authorization": "Bearer sk-demo-tenant-a"}
    resp = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "support-default",
            "messages": [{"role": "user", "content": "access locked m2b"}],
        },
    )
    assert resp.status_code == 200

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert "gateway_requests_total" in metrics.text

    traces = client.get("/v1/traces?limit=5")
    assert traces.status_code == 200
    data = traces.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # No prompt leakage in list API
    dumped = json.dumps(data)
    assert "messages" not in dumped
    assert "access locked" not in dumped


def main() -> None:
    _check_trace_happy_and_injection()
    _check_cache_hit_traced()
    _check_sqlite_store()
    _check_http_metrics_and_traces()
    # silence unused import lint on gateway re-export used by TestClient side effects
    assert gateway is not None
    print("M2b observability OK")


if __name__ == "__main__":
    main()
