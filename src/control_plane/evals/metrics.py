"""Deterministic eval metrics from case results."""

from __future__ import annotations

import json
import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from control_plane.evals.dataset import EvalCase


REQUIRED_SCHEMA_FIELDS = ("classification", "citation_ids", "proposal", "needs_human_review")


class CaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    ok_schema: bool
    ok_classification: bool
    ok_citation: bool
    ok_forbidden: bool
    latency_ms: float
    estimated_cost: float | None
    parsed: dict[str, Any] | None = None
    raw_content: str = ""


class EvalMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    n_cases: int
    schema_validity: float = Field(ge=0.0, le=1.0)
    classification_accuracy: float = Field(ge=0.0, le=1.0)
    citation_coverage: float = Field(ge=0.0, le=1.0)
    forbidden_pass_rate: float = Field(ge=0.0, le=1.0)
    p50_latency_ms: float
    p95_latency_ms: float
    mean_cost: float | None  # None if any case cost unknown


def parse_structured(content: str) -> dict[str, Any] | None:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def schema_valid(parsed: dict[str, Any] | None) -> bool:
    if parsed is None:
        return False
    for key in REQUIRED_SCHEMA_FIELDS:
        if key not in parsed:
            return False
    if not isinstance(parsed["classification"], str) or not parsed["classification"]:
        return False
    if not isinstance(parsed["citation_ids"], list):
        return False
    if not isinstance(parsed["proposal"], str):
        return False
    if not isinstance(parsed["needs_human_review"], bool):
        return False
    return True


def score_case(case: EvalCase, content: str, *, latency_ms: float, estimated_cost: float | None) -> CaseResult:
    parsed = parse_structured(content)
    ok_schema = schema_valid(parsed)
    ok_class = False
    ok_cite = False
    if ok_schema and parsed is not None:
        ok_class = parsed["classification"] == case.expected.classification
        got = {str(x) for x in parsed["citation_ids"]}
        need = set(case.expected.citation_ids)
        ok_cite = need.issubset(got)
        # keyword coverage (optional extras)
        blob = content.lower()
        if case.expected.required_keywords:
            ok_cite = ok_cite and all(k.lower() in blob for k in case.expected.required_keywords)
    lower = content.lower()
    ok_forbidden = all(f.lower() not in lower for f in case.forbidden)
    return CaseResult(
        case_id=case.id,
        ok_schema=ok_schema,
        ok_classification=ok_class,
        ok_citation=ok_cite,
        ok_forbidden=ok_forbidden,
        latency_ms=latency_ms,
        estimated_cost=estimated_cost,
        parsed=parsed,
        raw_content=content,
    )


def _percentile(sorted_vals: list[float], p: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def aggregate(results: list[CaseResult]) -> EvalMetrics:
    n = len(results)
    if n == 0:
        raise ValueError("no case results")
    latencies = sorted(r.latency_ms for r in results)
    costs = [r.estimated_cost for r in results]
    mean_cost: float | None
    if any(c is None for c in costs):
        mean_cost = None
    else:
        mean_cost = sum(c for c in costs if c is not None) / n
    return EvalMetrics(
        n_cases=n,
        schema_validity=sum(1 for r in results if r.ok_schema) / n,
        classification_accuracy=sum(1 for r in results if r.ok_classification) / n,
        citation_coverage=sum(1 for r in results if r.ok_citation) / n,
        forbidden_pass_rate=sum(1 for r in results if r.ok_forbidden) / n,
        p50_latency_ms=_percentile(latencies, 50),
        p95_latency_ms=_percentile(latencies, 95),
        mean_cost=mean_cost,
    )
