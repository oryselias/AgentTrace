"""Baseline comparison — hard release gate (HANDOFF §6)."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from control_plane.evals.metrics import EvalMetrics


class GateVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passed: bool
    failures: list[str] = Field(default_factory=list)
    candidate: EvalMetrics
    baseline: EvalMetrics


def load_baseline(path: Path) -> EvalMetrics:
    return EvalMetrics.model_validate(json.loads(path.read_text(encoding="utf-8")))


def save_baseline(path: Path, metrics: EvalMetrics) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(metrics.model_dump_json(indent=2) + "\n", encoding="utf-8")


def compare_to_baseline(
    candidate: EvalMetrics,
    baseline: EvalMetrics,
    *,
    allow_cost_override: bool = False,
    allow_latency_override: bool = False,
) -> GateVerdict:
    """Fail when any hard HANDOFF condition is met."""
    failures: list[str] = []

    if candidate.schema_validity < 1.0:
        failures.append(
            f"schema_validity {candidate.schema_validity:.4f} < 1.0 (required 100%)"
        )

    drop = baseline.classification_accuracy - candidate.classification_accuracy
    if drop > 0.02 + 1e-12:
        failures.append(
            f"classification_accuracy dropped {drop:.4f} (> 0.02 vs baseline "
            f"{baseline.classification_accuracy:.4f} -> {candidate.classification_accuracy:.4f})"
        )

    if candidate.citation_coverage < 0.95:
        failures.append(
            f"citation_coverage {candidate.citation_coverage:.4f} < 0.95"
        )

    if candidate.forbidden_pass_rate < 1.0:
        failures.append(
            f"forbidden_pass_rate {candidate.forbidden_pass_rate:.4f} < 1.0"
        )

    if not allow_cost_override:
        if baseline.mean_cost is None or candidate.mean_cost is None:
            if candidate.mean_cost is None and baseline.mean_cost is not None:
                failures.append("mean_cost unknown on candidate while baseline has cost")
        elif baseline.mean_cost > 0 and candidate.mean_cost > baseline.mean_cost * 1.20 + 1e-12:
            pct = (candidate.mean_cost / baseline.mean_cost - 1.0) * 100.0
            failures.append(
                f"mean_cost rose {pct:.1f}% (> 20%; {baseline.mean_cost:.6f} -> {candidate.mean_cost:.6f})"
            )

    if not allow_latency_override:
        if baseline.p95_latency_ms > 0 and candidate.p95_latency_ms > baseline.p95_latency_ms * 1.25 + 1e-12:
            pct = (candidate.p95_latency_ms / baseline.p95_latency_ms - 1.0) * 100.0
            failures.append(
                f"p95_latency_ms rose {pct:.1f}% (> 25%; "
                f"{baseline.p95_latency_ms:.3f} -> {candidate.p95_latency_ms:.3f})"
            )

    return GateVerdict(
        passed=not failures,
        failures=failures,
        candidate=candidate,
        baseline=baseline,
    )
