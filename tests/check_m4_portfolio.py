"""M4 portfolio proof self-check. Run: python -m tests.check_m4_portfolio"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from control_plane.settings import ROOT

COMPOSE = ROOT / "docker-compose.yml"
DOCKERFILE = ROOT / "Dockerfile"
DEMO = ROOT / "scripts" / "demo.py"
BENCH = ROOT / "scripts" / "bench.py"
ARCH = ROOT / "docs" / "architecture.md"
README = ROOT / "README.md"
RESULTS = ROOT / "docs" / "measured-results.json"
REPORT = ROOT / "docs" / "measured-report.md"


def _check_artifacts_exist() -> None:
    for path in (COMPOSE, DOCKERFILE, DEMO, BENCH, ARCH, README):
        assert path.is_file(), f"missing {path.relative_to(ROOT)}"


def _check_demo_and_bench() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "measured-results.json"
        demo = subprocess.run(
            [sys.executable, str(DEMO), "--write-report", str(out)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert demo.returncode == 0, demo.stderr + demo.stdout

        bench = subprocess.run(
            [sys.executable, str(BENCH), "--n", "80", "--workers", "4", "--write-report", str(out)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        assert bench.returncode == 0, bench.stderr + bench.stdout

        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["demo"]["ok"] is True
        assert data["demo"]["steps"]["gate_fail"]["passed"] is False
        assert data["demo"]["steps"]["gate_pass"]["passed"] is True
        assert data["bench"]["throughput"]["n"] == 80
        assert data["bench"]["throughput"]["throughput_rps"] > 0
        assert data["bench"]["fallback"]["fallback_success_rate"] == 1.0
        assert data["bench"]["idempotency"]["charged_once"] is True


def _check_readme_mentions_measured() -> None:
    text = README.read_text(encoding="utf-8")
    assert "docker compose" in text.lower() or "docker-compose" in text.lower()
    assert "measured" in text.lower()
    assert ARCH.is_file()
    assert RESULTS.is_file(), "commit docs/measured-results.json from scripts/bench.py"
    assert REPORT.is_file()


def main() -> None:
    _check_artifacts_exist()
    _check_demo_and_bench()
    _check_readme_mentions_measured()
    print("check_m4_portfolio: ok")


if __name__ == "__main__":
    main()
