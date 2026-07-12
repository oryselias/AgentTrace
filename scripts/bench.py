"""Measured load/cache/fallback bench. Fake provider only.

Run from repo root:
  python scripts/bench.py
  python scripts/bench.py --write-report docs/measured-results.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from control_plane.gateway import Gateway  # noqa: E402
from control_plane.policies import Tenant, hash_api_key  # noqa: E402
from control_plane.providers.fake import FakeProvider  # noqa: E402
from control_plane.schemas import (  # noqa: E402
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    GatewayErrorBody,
)
from control_plane.settings import load_route  # noqa: E402
from control_plane.traces import MemoryTraceStore  # noqa: E402


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _tenant(api_key: str, *, rpm: int = 10_000) -> Tenant:
    return Tenant(
        tenant_id="bench",
        key_id="k_bench",
        key_hash=hash_api_key(api_key),
        rpm=rpm,
        daily_budget_usd=1_000.0,
        cost_per_1k_tokens=0.002,
    )


def _req(content: str) -> ChatCompletionRequest:
    return ChatCompletionRequest(
        model="support-default",
        messages=[ChatMessage(role="user", content=content)],
    )


def bench_throughput(n: int = 200, workers: int = 8) -> dict:
    FakeProvider.reset_fail_once()
    route = load_route(ROOT / "configs" / "support-v1.yaml")
    store = MemoryTraceStore()
    gw = Gateway(route, tenants=[_tenant("sk-bench")], traces=store)

    def one(i: int) -> float:
        t0 = time.perf_counter()
        result = gw.complete_chat(
            _req(f"billing case {i % 17}"),
            request_id=f"bench_{i}",
            api_key="sk-bench",
        )
        assert isinstance(result, ChatCompletionResponse), result
        return (time.perf_counter() - t0) * 1000.0

    wall0 = time.perf_counter()
    latencies: list[float] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = [pool.submit(one, i) for i in range(n)]
        for fut in as_completed(futs):
            latencies.append(fut.result())
    wall = time.perf_counter() - wall0
    latencies.sort()
    return {
        "n": n,
        "workers": workers,
        "wall_s": round(wall, 4),
        "throughput_rps": round(n / wall, 1) if wall > 0 else 0.0,
        "p50_ms": round(_percentile(latencies, 50), 3),
        "p95_ms": round(_percentile(latencies, 95), 3),
        "mean_ms": round(statistics.fmean(latencies), 3),
    }


def bench_cache(rounds: int = 50) -> dict:
    FakeProvider.reset_fail_once()
    route = load_route(ROOT / "configs" / "support-v1.yaml")
    store = MemoryTraceStore()
    gw = Gateway(route, tenants=[_tenant("sk-cache")], traces=store)
    content = "access locked cache bench unique"

    miss_ms: list[float] = []
    hit_ms: list[float] = []
    for i in range(rounds):
        msg = f"{content} {i}"
        t0 = time.perf_counter()
        r1 = gw.complete_chat(_req(msg), request_id=f"miss_{i}", api_key="sk-cache")
        miss_ms.append((time.perf_counter() - t0) * 1000.0)
        assert isinstance(r1, ChatCompletionResponse)
        t1 = time.perf_counter()
        r2 = gw.complete_chat(_req(msg), request_id=f"hit_{i}", api_key="sk-cache")
        hit_ms.append((time.perf_counter() - t1) * 1000.0)
        assert isinstance(r2, ChatCompletionResponse)
        assert store.list_recent(limit=1)[0].cache_hit is True

    miss_ms.sort()
    hit_ms.sort()
    miss_p50 = _percentile(miss_ms, 50)
    hit_p50 = _percentile(hit_ms, 50)
    return {
        "rounds": rounds,
        "miss_p50_ms": round(miss_p50, 3),
        "miss_p95_ms": round(_percentile(miss_ms, 95), 3),
        "hit_p50_ms": round(hit_p50, 3),
        "hit_p95_ms": round(_percentile(hit_ms, 95), 3),
        "p50_speedup_x": round(miss_p50 / hit_p50, 2) if hit_p50 > 0 else None,
    }


def bench_fallback(n: int = 40) -> dict:
    FakeProvider.reset_fail_once()
    route = load_route(ROOT / "configs" / "support-v1.yaml")
    store = MemoryTraceStore()
    gw = Gateway(route, tenants=[_tenant("sk-fb")], traces=store)
    ok = 0
    for i in range(n):
        result = gw.complete_chat(
            _req(f"[[fake:unavailable:fake-support]] outage ticket {i}"),
            request_id=f"fb_{i}",
            api_key="sk-fb",
        )
        if isinstance(result, ChatCompletionResponse):
            tr = store.list_recent(limit=1)[0]
            if tr.fallback_used:
                ok += 1
        else:
            assert isinstance(result, GatewayErrorBody)
    return {
        "n": n,
        "fallback_success": ok,
        "fallback_success_rate": round(ok / n, 3) if n else 0.0,
    }


def bench_idempotency() -> dict:
    FakeProvider.reset_fail_once()
    route = load_route(ROOT / "configs" / "support-v1.yaml")
    gw = Gateway(route, tenants=[_tenant("sk-idem")])
    before = gw.budget.spent("bench")
    r1 = gw.complete_chat(
        _req("refund idempotency bench"),
        request_id="idem_a",
        api_key="sk-idem",
        idempotency_key="bench-key-1",
    )
    mid = gw.budget.spent("bench")
    r2 = gw.complete_chat(
        _req("refund idempotency bench"),
        request_id="idem_b",
        api_key="sk-idem",
        idempotency_key="bench-key-1",
    )
    after = gw.budget.spent("bench")
    assert isinstance(r1, ChatCompletionResponse)
    assert isinstance(r2, ChatCompletionResponse)
    return {
        "charged_once": after == mid and mid > before,
        "spent_usd": mid,
        "replay_spent_usd": after,
    }


def run_bench(*, n: int, workers: int) -> dict:
    print("=== throughput ===")
    tp = bench_throughput(n=n, workers=workers)
    print(json.dumps(tp, indent=2))

    print("\n=== cache ===")
    cache = bench_cache()
    print(json.dumps(cache, indent=2))

    print("\n=== fallback ===")
    fb = bench_fallback()
    print(json.dumps(fb, indent=2))

    print("\n=== idempotency ===")
    idem = bench_idempotency()
    print(json.dumps(idem, indent=2))

    return {
        "provider": "fake",
        "throughput": tp,
        "cache": cache,
        "fallback": fb,
        "idempotency": idem,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Measured bench for portfolio report")
    parser.add_argument("--n", type=int, default=200, help="Throughput request count")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--write-report", default=None)
    args = parser.parse_args()
    results = run_bench(n=args.n, workers=args.workers)
    if args.write_report:
        path = Path(args.write_report)
        if not path.is_absolute():
            path = ROOT / path
        existing: dict = {}
        if path.is_file():
            existing = json.loads(path.read_text(encoding="utf-8"))
        existing["bench"] = results
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
