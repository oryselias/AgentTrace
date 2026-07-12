"""M0 contract freeze self-check. Run: python -m tests.check_m0_contracts"""

from __future__ import annotations

from pathlib import Path

import yaml

from control_plane.context import RequestContext
from control_plane.providers.base import Provider, ProviderRequest, ProviderSuccess
from control_plane.schemas import (
    TRANSIENT_ERRORS,
    ChatCompletionRequest,
    ChatMessage,
    ErrorCategory,
    RouteConfig,
    TraceRecord,
)


ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "support-v1.yaml"


def _load_route() -> RouteConfig:
    raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    return RouteConfig.model_validate(raw)


def main() -> None:
    assert CONFIG_PATH.is_file(), f"missing {CONFIG_PATH}"
    # Default loaders must resolve configs/ after pip install (Docker WORKDIR) and editable src.
    from control_plane.settings import DEFAULT_CONFIG, load_route

    assert DEFAULT_CONFIG.is_file(), f"settings ROOT missed configs: {DEFAULT_CONFIG}"
    assert load_route().version == "support-v1"
    route = _load_route()
    assert route.version == "support-v1"
    assert route.alias == "support-default"
    assert route.max_retries == 1
    assert route.fallback is not None
    assert route.primary.provider == "fake"

    req = ChatCompletionRequest(
        model=route.alias,
        messages=[ChatMessage(role="user", content="escalate: billing")],
    )
    ctx = RequestContext(
        request_id="req_m0",
        trace_id="tr_m0",
        tenant_id="tenant_demo",
        api_key_id="key_demo",
        config_version=route.version,
        logical_alias=route.alias,
        request=req,
    )
    assert ctx.config_version == route.version

    # Protocol structural check: a tiny conforming stub is enough for M0.
    class _Fake:
        name = "fake"

        def complete(self, request: ProviderRequest) -> ProviderSuccess:
            return ProviderSuccess(
                content='{"ok":true}',
                prompt_tokens=1,
                completion_tokens=1,
                latency_ms=1.0,
                raw_model=request.model,
            )

    fake: Provider = _Fake()
    result = fake.complete(
        ProviderRequest(model=route.primary.model, messages=[{"role": "user", "content": "x"}], timeout_ms=100)
    )
    assert isinstance(result, ProviderSuccess)

    assert ErrorCategory.TIMEOUT in TRANSIENT_ERRORS
    assert ErrorCategory.PERMANENT_CLIENT_ERROR not in TRANSIENT_ERRORS

    TraceRecord(
        request_id=ctx.request_id,
        trace_id=ctx.trace_id,
        tenant_id=ctx.tenant_id,
        logical_alias=ctx.logical_alias,
        config_version=ctx.config_version,
    )

    print("M0 contract freeze OK")


if __name__ == "__main__":
    main()
