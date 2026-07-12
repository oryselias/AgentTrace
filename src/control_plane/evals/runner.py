"""Run a candidate RouteConfig against the eval dataset via the fake provider."""

from __future__ import annotations

from pathlib import Path

from control_plane.evals.dataset import EvalCase, load_jsonl
from control_plane.evals.metrics import CaseResult, EvalMetrics, aggregate, score_case
from control_plane.policies import estimate_cost_usd
from control_plane.providers.base import ProviderFailure, ProviderRequest, ProviderSuccess
from control_plane.providers.fake import FakeProvider
from control_plane.schemas import RouteConfig
from control_plane.settings import ROOT, load_route

DEFAULT_DATASET = ROOT / "evals" / "escalations.jsonl"
# Fixed rate for comparable cost metrics across candidates (not tenant billing).
EVAL_COST_PER_1K = 0.002


def _messages_as_dicts(case: EvalCase) -> list[dict[str, str]]:
    return [{"role": m.role, "content": m.content} for m in case.messages]


def run_case(provider: FakeProvider, route: RouteConfig, case: EvalCase) -> CaseResult:
    endpoint = route.primary
    req = ProviderRequest(
        model=endpoint.model,
        messages=_messages_as_dicts(case),
        timeout_ms=endpoint.timeout_ms,
        max_tokens=route.max_output_tokens,
    )
    result = provider.complete(req)
    if isinstance(result, ProviderFailure):
        return CaseResult(
            case_id=case.id,
            ok_schema=False,
            ok_classification=False,
            ok_citation=False,
            ok_forbidden=True,
            latency_ms=result.latency_ms,
            estimated_cost=None,
            parsed=None,
            raw_content="",
        )
    assert isinstance(result, ProviderSuccess)
    cost = estimate_cost_usd(result.prompt_tokens, result.completion_tokens, EVAL_COST_PER_1K)
    return score_case(case, result.content, latency_ms=result.latency_ms, estimated_cost=cost)


def run_eval(
    route: RouteConfig,
    *,
    dataset_path: Path | None = None,
    provider: FakeProvider | None = None,
) -> tuple[EvalMetrics, list[CaseResult]]:
    cases = load_jsonl(dataset_path or DEFAULT_DATASET)
    fake = provider or FakeProvider()
    results = [run_case(fake, route, case) for case in cases]
    return aggregate(results), results


def run_eval_from_config(
    config_path: Path,
    *,
    dataset_path: Path | None = None,
) -> tuple[RouteConfig, EvalMetrics, list[CaseResult]]:
    route = load_route(config_path)
    metrics, results = run_eval(route, dataset_path=dataset_path)
    return route, metrics, results
