"""M3 eval gate self-check. Run: python -m tests.check_m3_eval_gate"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from control_plane.evals.dataset import load_jsonl, load_policies
from control_plane.evals.gate import compare_to_baseline, load_baseline
from control_plane.evals.metrics import aggregate, score_case
from control_plane.evals.runner import run_eval_from_config
from control_plane.settings import ROOT


DATASET = ROOT / "evals" / "escalations.jsonl"
POLICIES = ROOT / "evals" / "policies.json"
BASELINE = ROOT / "evals" / "baselines" / "support-v1.json"
GOOD = ROOT / "configs" / "support-v1.yaml"
BAD = ROOT / "configs" / "support-bad.yaml"


def _check_fixtures() -> None:
    cases = load_jsonl(DATASET)
    assert len(cases) >= 20, f"need >=20 cases, got {len(cases)}"
    policies = load_policies(POLICIES)
    for case in cases:
        for cid in case.expected.citation_ids:
            assert cid in policies, f"{case.id}: missing policy {cid}"


def _check_metrics_unit() -> None:
    from control_plane.evals.dataset import EvalCase, EvalExpected
    from control_plane.schemas import ChatMessage

    case = EvalCase(
        id="u1",
        messages=[ChatMessage(role="user", content="billing charge")],
        expected=EvalExpected(classification="billing", citation_ids=["pol-billing"]),
    )
    good = score_case(
        case,
        '{"classification":"billing","citation_ids":["pol-billing"],"proposal":"x","needs_human_review":true}',
        latency_ms=2.0,
        estimated_cost=0.001,
    )
    assert good.ok_schema and good.ok_classification and good.ok_citation
    bad = score_case(case, "not-json", latency_ms=2.0, estimated_cost=0.001)
    assert not bad.ok_schema
    m = aggregate([good, bad])
    assert m.schema_validity == 0.5
    assert m.n_cases == 2


def _check_gate_thresholds() -> None:
    from control_plane.evals.metrics import EvalMetrics

    baseline = EvalMetrics(
        n_cases=10,
        schema_validity=1.0,
        classification_accuracy=1.0,
        citation_coverage=1.0,
        forbidden_pass_rate=1.0,
        p50_latency_ms=1.0,
        p95_latency_ms=1.0,
        mean_cost=0.001,
    )
    ok = baseline.model_copy()
    assert compare_to_baseline(ok, baseline).passed

    drop = baseline.model_copy(update={"classification_accuracy": 0.97})
    assert not compare_to_baseline(drop, baseline).passed

    cite = baseline.model_copy(update={"citation_coverage": 0.94})
    assert not compare_to_baseline(cite, baseline).passed

    schema = baseline.model_copy(update={"schema_validity": 0.99})
    assert not compare_to_baseline(schema, baseline).passed

    cost = baseline.model_copy(update={"mean_cost": 0.001 * 1.21})
    assert not compare_to_baseline(cost, baseline).passed
    assert compare_to_baseline(cost, baseline, allow_cost_override=True).passed

    lat = baseline.model_copy(update={"p95_latency_ms": 1.26})
    assert not compare_to_baseline(lat, baseline).passed
    assert compare_to_baseline(lat, baseline, allow_latency_override=True).passed


def _ensure_baseline() -> None:
    if BASELINE.is_file():
        return
    route, metrics, _ = run_eval_from_config(GOOD)
    assert route.version == "support-v1"
    BASELINE.parent.mkdir(parents=True, exist_ok=True)
    BASELINE.write_text(metrics.model_dump_json(indent=2) + "\n", encoding="utf-8")


def _check_good_passes() -> None:
    _ensure_baseline()
    _route, metrics, results = run_eval_from_config(GOOD)
    assert all(r.ok_schema for r in results)
    baseline = load_baseline(BASELINE)
    verdict = compare_to_baseline(metrics, baseline)
    assert verdict.passed, verdict.failures


def _check_bad_fails() -> None:
    _ensure_baseline()
    _route, metrics, _ = run_eval_from_config(BAD)
    baseline = load_baseline(BASELINE)
    verdict = compare_to_baseline(metrics, baseline)
    assert not verdict.passed, "degraded candidate must fail the gate"
    assert any("citation_coverage" in f or "classification_accuracy" in f for f in verdict.failures)


def _check_cli_exit_codes() -> None:
    _ensure_baseline()
    good = subprocess.run(
        [sys.executable, "-m", "control_plane", "eval", "--candidate", str(GOOD), "--baseline", str(BASELINE)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr + good.stdout
    payload = json.loads(good.stdout)
    assert payload["gate"]["passed"] is True

    bad = subprocess.run(
        [sys.executable, "-m", "control_plane", "eval", "--candidate", str(BAD), "--baseline", str(BASELINE)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad.returncode == 1, f"expected exit 1, got {bad.returncode}: {bad.stdout}"
    payload = json.loads(bad.stdout)
    assert payload["gate"]["passed"] is False
    assert payload["gate"]["failures"]


def main() -> None:
    _check_fixtures()
    _check_metrics_unit()
    _check_gate_thresholds()
    _check_good_passes()
    _check_bad_fails()
    _check_cli_exit_codes()
    print("check_m3_eval_gate: ok")


if __name__ == "__main__":
    main()
