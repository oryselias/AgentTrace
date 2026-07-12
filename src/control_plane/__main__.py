"""CLI: python -m control_plane eval --candidate configs/support-v1.yaml"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from control_plane.evals.gate import compare_to_baseline, load_baseline, save_baseline
from control_plane.evals.runner import run_eval_from_config
from control_plane.settings import ROOT


def _cmd_eval(args: argparse.Namespace) -> int:
    candidate = Path(args.candidate)
    if not candidate.is_file():
        candidate = ROOT / candidate
    dataset = Path(args.dataset) if args.dataset else None
    if dataset is not None and not dataset.is_file():
        dataset = ROOT / dataset

    route, metrics, _results = run_eval_from_config(candidate, dataset_path=dataset)
    report = {
        "config_version": route.version,
        "alias": route.alias,
        "metrics": metrics.model_dump(),
    }

    if args.write_baseline:
        out = Path(args.write_baseline)
        if not out.is_absolute():
            out = ROOT / out
        save_baseline(out, metrics)
        report["baseline_written"] = str(out)
        print(json.dumps(report, indent=2))
        return 0

    baseline_path = Path(args.baseline) if args.baseline else ROOT / "evals" / "baselines" / "support-v1.json"
    if not baseline_path.is_file():
        baseline_path = ROOT / baseline_path
    if not baseline_path.is_file():
        print(f"missing baseline: {baseline_path}", file=sys.stderr)
        print("hint: python -m control_plane eval --candidate configs/support-v1.yaml --write-baseline evals/baselines/support-v1.json", file=sys.stderr)
        return 2

    baseline = load_baseline(baseline_path)
    verdict = compare_to_baseline(
        metrics,
        baseline,
        allow_cost_override=args.allow_cost_override,
        allow_latency_override=args.allow_latency_override,
    )
    report["gate"] = {
        "passed": verdict.passed,
        "failures": verdict.failures,
        "baseline": baseline.model_dump(),
    }
    print(json.dumps(report, indent=2))
    return 0 if verdict.passed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="control_plane")
    sub = parser.add_subparsers(dest="command", required=True)

    ev = sub.add_parser("eval", help="Run eval dataset and compare to baseline")
    ev.add_argument("--candidate", required=True, help="Path to route YAML")
    ev.add_argument("--dataset", default=None, help="Eval JSONL (default: evals/escalations.jsonl)")
    ev.add_argument("--baseline", default=None, help="Baseline metrics JSON")
    ev.add_argument(
        "--write-baseline",
        default=None,
        metavar="PATH",
        help="Write candidate metrics as baseline and exit 0 (no gate)",
    )
    ev.add_argument("--allow-cost-override", action="store_true")
    ev.add_argument("--allow-latency-override", action="store_true")
    ev.set_defaults(func=_cmd_eval)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
