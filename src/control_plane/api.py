"""HTTP boundary only. Orchestration lives in gateway.py."""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Header, Query, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from control_plane.gateway import Gateway
from control_plane.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ErrorCategory,
    GatewayErrorBody,
)
from control_plane.settings import load_route, load_tenants

app = FastAPI(title="llm-reliability-control-plane", version="0.0.1")
gateway = Gateway(load_route(), tenants=load_tenants())


def _extract_api_key(
    authorization: str | None,
    x_api_key: str | None,
) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def _status_for(result: GatewayErrorBody) -> int:
    if result.error == ErrorCategory.RATE_LIMITED:
        return 429
    if result.error == ErrorCategory.PERMANENT_CLIENT_ERROR and "api key" in result.message:
        return 401
    if not result.retryable:
        return 400
    return 503


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "config_version": gateway.route.version}


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(gateway.metrics.render_prometheus(), media_type="text/plain; version=0.0.4")


@app.get("/v1/traces")
def list_traces(limit: int = Query(default=50, ge=1, le=500)) -> JSONResponse:
    rows = gateway.traces.list_recent(limit=limit)
    return JSONResponse(content=[r.model_dump(mode="json") for r in rows])


@app.post("/v1/chat/completions")
def chat_completions(
    body: ChatCompletionRequest,
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    rid = f"req_{uuid.uuid4().hex[:12]}"
    result = gateway.complete_chat(
        body,
        request_id=rid,
        api_key=_extract_api_key(authorization, x_api_key),
        idempotency_key=idempotency_key,
    )
    if isinstance(result, GatewayErrorBody):
        return JSONResponse(status_code=_status_for(result), content=result.model_dump(mode="json"))
    assert isinstance(result, ChatCompletionResponse)
    return JSONResponse(status_code=200, content=result.model_dump(mode="json"))
