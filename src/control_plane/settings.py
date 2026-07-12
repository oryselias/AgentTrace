"""Minimal runtime settings. Route + tenant loading."""

from __future__ import annotations

from pathlib import Path

import yaml

from control_plane.policies import Tenant, hash_api_key
from control_plane.schemas import RouteConfig


def _repo_root() -> Path:
    """Directory that owns configs/ and evals/.

    Editable/src layout: parents[2] from this file is the repo root.
    Docker `pip install .`: package lands in site-packages; WORKDIR (/app) has configs.
    """
    from_src = Path(__file__).resolve().parents[2]
    if (from_src / "configs").is_dir():
        return from_src
    cwd = Path.cwd()
    if (cwd / "configs").is_dir():
        return cwd
    return from_src


ROOT = _repo_root()
DEFAULT_CONFIG = ROOT / "configs" / "support-v1.yaml"
DEFAULT_TENANTS = ROOT / "configs" / "tenants.yaml"


def load_route(path: Path | None = None) -> RouteConfig:
    cfg = path or DEFAULT_CONFIG
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    return RouteConfig.model_validate(raw)


def load_tenants(path: Path | None = None) -> list[Tenant]:
    cfg = path or DEFAULT_TENANTS
    if not cfg.is_file():
        return []
    raw = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    out: list[Tenant] = []
    for row in raw.get("tenants") or []:
        api_key = str(row["api_key"])
        cost = row.get("cost_per_1k_tokens")
        out.append(
            Tenant(
                tenant_id=str(row["tenant_id"]),
                key_id=str(row["key_id"]),
                key_hash=hash_api_key(api_key),
                rpm=int(row["rpm"]),
                daily_budget_usd=float(row["daily_budget_usd"]),
                cost_per_1k_tokens=float(cost) if cost is not None else None,
            )
        )
    return out
