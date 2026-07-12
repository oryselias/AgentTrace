"""Reproducible portfolio demo — HANDOFF §7 scenarios + eval gate.

Run from repo root:
  python scripts/demo.py
  python scripts/demo.py --write-report docs/measured-results.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane.evals.gate import compare_to_baseline, load_baseline  # noqa: E402
from control_plane.evals.runner import run_eval_from_config  # noqa: E402
from control_plane.gateway import Gateway  # noqa: E402
from control_plane.policies import Tenant, hash_api_key  # noqa: E402
from control_plane.providers.fake import FakeProvider  # noqa: E402
from control_plane.schemas import (  # noqa: E402
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
)
from control_plane.settings import load_route  # noqa: E402
from control_plane.traces import MemoryTraceStore  # noqa: E402


API_KEY = "sk-demo"
TENANT = Tenant(
    tenant_id="demo",
    key_id="k_demo",
    key_hash=hash_api_key(API_KEY),
    rpm=120,
    daily_budget_usd=10.0,
    cost_per_1k_tokens=0.002,
)


def _req(content: str, **kwargs: object) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="support-default",
        messages=[ChatMessage(role="user", content=content)],
        **kwargs,  # type: ignore[arg-type]
    )


def _step(n: int, title: str) -> None:
    print(f"\n=== {n}. {title} ===")


def run_demo() -> dict:
    FakeProvider.reset_fail_once()
    route = load_route(ROOT / "configs" / "support-v1.yaml")
    store = MemoryTraceStore()
    gw = Gateway(route, tenants=[TENANT], traces=store)
    out: dict = {"steps": {}}

    # 1. Valid primary route
    _step(1, "Valid request -> primary")
    r1 = gw.complete_chat(_req("billing dispute on last invoice"), request_id="demo_1", api_key=API_KEY)
    assert isinstance(r1, ChatCompletionResponse), r1
    body = json.loads(r1.choices[0].message.content)
    tr1 = store.list_recent(limit=1)[0]
    print(f"classification={body['classification']} provider={tr1.resolved_provider} model={tr1.resolved_model}")
    assert body["classification"] == "billing"
    assert tr1.resolved_model == "fake-support"
    assert tr1.fallback_used is False
    out["steps"]["primary"] = {
        "ok": True,
        "classification": body["classification"],
        "resolved_model": tr1.resolved_model,
        "e2e_ms": tr1.end_to_end_latency_ms,
    }

    # 2. Cache hit
    _step(2, "Repeated request -> exact cache")
    r2 = gw.complete_chat(_req("billing dispute on last invoice"), request_id="demo_2", api_key=API_KEY)
    hit_tr = store.list_recent(limit=1)[0]
    assert isinstance(r2, ChatCompletionResponse)
    assert hit_tr.cache_hit is True
    print(f"cache_hit={hit_tr.cache_hit} e2e_ms={hit_tr.end_to_end_latency_ms:.3f}")
    out["steps"]["cache"] = {
        "ok": True,
        "cache_hit": True,
        "hit_e2e_ms": hit_tr.end_to_end_latency_ms,
        "prior_miss_e2e_ms": tr1.end_to_end_latency_ms,
    }

    # 3. PII redaction
    _step(3, "PII redaction in audit metadata")
    r3 = gw.complete_chat(
        _req("refund for jane.doe@example.com phone +1 (415) 555-0100 ACCT-998877"),
        request_id="demo_3",
        api_key=API_KEY,
    )
    assert isinstance(r3, ChatCompletionResponse)
    tr3 = store.list_recent(limit=1)[0]
    blob = tr3.model_dump_json()
    assert tr3.redaction_applied is True
    assert "jane.doe@example.com" not in blob
    assert "555-0100" not in blob
    assert "ACCT-998877" not in blob
    print(f"redaction_applied={tr3.redaction_applied} (no raw PII in trace JSON)")
    out["steps"]["pii"] = {"ok": True, "redaction_applied": True}

    # 4. Timeout -> fallback
    _step(4, "Provider timeout -> fallback")
    FakeProvider.reset_fail_once()
    r4 = gw.complete_chat(
        _req("[[fake:timeout:fake-support]] outage affecting EU region"),
        request_id="demo_4",
        api_key=API_KEY,
    )
    assert isinstance(r4, ChatCompletionResponse), r4
    tr4 = store.list_recent(limit=1)[0]
    assert tr4.fallback_used is True
    assert tr4.resolved_model == "fake-support-fallback"
    print(f"fallback_used={tr4.fallback_used} model={tr4.resolved_model}")
    out["steps"]["fallback"] = {
        "ok": True,
        "fallback_used": True,
        "resolved_model": tr4.resolved_model,
    }

    # 5. Invalid response on primary -> fallback (schema repair not in v1)
    _step(5, "Invalid structured output on primary -> fallback")
    FakeProvider.reset_fail_once()
    r5 = gw.complete_chat(
        _req("[[fake:invalid-response:fake-support]] access locked account"),
        request_id="demo_5",
        api_key=API_KEY,
    )
    assert isinstance(r5, ChatCompletionResponse), r5
    tr5 = store.list_recent(limit=1)[0]
    assert tr5.fallback_used is True
    print(f"invalid-response on primary -> fallback model={tr5.resolved_model}")
    out["steps"]["invalid_response_fallback"] = {
        "ok": True,
        "fallback_used": True,
        "resolved_model": tr5.resolved_model,
        "note": "v1 falls back on invalid-response; no single-shot repair loop yet",
    }

    # 6–7. Eval gate
    _step(6, "Degraded candidate fails release gate")
    baseline = load_baseline(ROOT / "evals" / "baselines" / "support-v1.json")
    _bad_route, bad_metrics, _ = run_eval_from_config(ROOT / "configs" / "support-bad.yaml")
    bad_verdict = compare_to_baseline(bad_metrics, baseline)
    assert not bad_verdict.passed, bad_verdict.failures
    print("failures:", "; ".join(bad_verdict.failures))
    out["steps"]["gate_fail"] = {
        "ok": True,
        "passed": False,
        "failures": bad_verdict.failures,
        "metrics": bad_metrics.model_dump(),
    }

    _step(7, "Baseline candidate passes release gate")
    _good_route, good_metrics, _ = run_eval_from_config(ROOT / "configs" / "support-v1.yaml")
    good_verdict = compare_to_baseline(good_metrics, baseline)
    assert good_verdict.passed, good_verdict.failures
    print(
        f"passed schema={good_metrics.schema_validity} "
        f"class={good_metrics.classification_accuracy} "
        f"cite={good_metrics.citation_coverage} "
        f"mean_cost={good_metrics.mean_cost:.6f}"
    )
    out["steps"]["gate_pass"] = {
        "ok": True,
        "passed": True,
        "metrics": good_metrics.model_dump(),
        "baseline": baseline.model_dump(),
    }

    # Bonus: idempotency does not double-bill
    _step(8, "Idempotent replay does not double-bill")
    before = gw.budget.spent("demo")
    first = gw.complete_chat(
        _req("security alert unique idempo demo"),
        request_id="demo_8a",
        api_key=API_KEY,
        idempotency_key="idem-demo-1",
    )
    mid = gw.budget.spent("demo")
    second = gw.complete_chat(
        _req("security alert unique idempo demo"),
        request_id="demo_8b",
        api_key=API_KEY,
        idempotency_key="idem-demo-1",
    )
    after = gw.budget.spent("demo")
    assert isinstance(first, ChatCompletionResponse)
    assert isinstance(second, ChatCompletionResponse)
    assert mid > before
    assert after == mid
    print(f"spent_before={before:.6f} after_first={mid:.6f} after_replay={after:.6f}")
    out["steps"]["idempotency"] = {
        "ok": True,
        "charged_once": True,
        "spent_usd": mid,
    }

    out["ok"] = True
    print("\nDemo OK")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Portfolio demo for LLM reliability control plane")
    parser.add_argument(
        "--write-report",
        default=None,
        help="Merge demo results into this JSON path",
    )
    args = parser.parse_args()
    report = run_demo()
    if args.write_report:
        path = Path(args.write_report)
        if not path.is_absolute():
            path = ROOT / path
        existing: dict = {}
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
        existing["demo"] = report
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
