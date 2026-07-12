"""OpenAI-compatible adapter self-check (httpx mock; no paid key).

Run: python -m tests.check_openai_compatible
"""

from __future__ import annotations

import json

import httpx

from control_plane.providers.base import ProviderFailure, ProviderRequest, ProviderSuccess
from control_plane.providers.fake import FakeProvider
from control_plane.providers.openai_compatible import OpenAICompatibleProvider
from control_plane.schemas import ErrorCategory


def _openai_ok_body(*, content: str, model: str = "gpt-test") -> dict:
    return {
        "id": "chatcmpl_test",
        "object": "chat.completion",
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 5, "total_tokens": 8},
    }


def _mock_handler(request: httpx.Request) -> httpx.Response:
    assert request.url.path.endswith("/chat/completions")
    assert request.headers.get("Authorization") == "Bearer test-key"
    body = json.loads(request.content.decode())
    user = ""
    for msg in reversed(body.get("messages") or []):
        if msg.get("role") == "user":
            user = str(msg.get("content") or "")
            break

    if "[[http:429]]" in user:
        return httpx.Response(429, json={"error": {"message": "rate"}})
    if "[[http:500]]" in user:
        return httpx.Response(500, json={"error": {"message": "boom"}})
    if "[[http:401]]" in user:
        return httpx.Response(401, json={"error": {"message": "bad key"}})
    if "[[http:bad-json]]" in user:
        return httpx.Response(200, content=b"not-json", headers={"content-type": "text/plain"})
    if "[[http:no-choices]]" in user:
        return httpx.Response(200, json={"id": "x", "choices": [], "model": "gpt-test"})
    if "[[http:timeout]]" in user:
        raise httpx.ReadTimeout("simulated timeout")

    content = json.dumps(
        {
            "classification": "billing" if "billing" in user.lower() else "general",
            "citation_ids": ["pol-billing"] if "billing" in user.lower() else ["pol-general"],
            "proposal": "ok",
            "needs_human_review": True,
        },
        separators=(",", ":"),
    )
    return httpx.Response(200, json=_openai_ok_body(content=content, model=body.get("model", "gpt-test")))


def _provider() -> OpenAICompatibleProvider:
    transport = httpx.MockTransport(_mock_handler)
    client = httpx.Client(transport=transport)
    return OpenAICompatibleProvider(api_key="test-key", base_url="https://example.test/v1", client=client)


def _req(text: str, *, model: str = "gpt-test") -> ProviderRequest:
    return ProviderRequest(
        model=model,
        messages=[{"role": "user", "content": text}],
        timeout_ms=200,
    )


def _check_success_and_contract_shape() -> None:
    openai = _provider()
    fake = FakeProvider()
    text = "escalate: billing dispute"
    o = openai.complete(_req(text, model="gpt-test"))
    f = fake.complete(_req(text, model="fake-support"))
    assert isinstance(o, ProviderSuccess)
    assert isinstance(f, ProviderSuccess)
    # Shared success envelope fields both adapters must populate.
    for result in (o, f):
        assert isinstance(result.content, str) and result.content
        assert result.prompt_tokens >= 0
        assert result.completion_tokens >= 0
        assert result.latency_ms >= 0
        assert isinstance(result.raw_model, str) and result.raw_model
    assert json.loads(o.content)["classification"] == "billing"
    assert o.prompt_tokens == 3
    assert o.completion_tokens == 5
    assert o.raw_model == "gpt-test"
    openai.close()


def _check_error_mapping() -> None:
    openai = _provider()
    cases = [
        ("[[http:429]]", ErrorCategory.RATE_LIMITED),
        ("[[http:500]]", ErrorCategory.UNAVAILABLE),
        ("[[http:401]]", ErrorCategory.PERMANENT_CLIENT_ERROR),
        ("[[http:bad-json]]", ErrorCategory.INVALID_RESPONSE),
        ("[[http:no-choices]]", ErrorCategory.INVALID_RESPONSE),
        ("[[http:timeout]]", ErrorCategory.TIMEOUT),
    ]
    for text, expected in cases:
        result = openai.complete(_req(text))
        assert isinstance(result, ProviderFailure), text
        assert result.category == expected, (text, result.category)
        assert "Bearer" not in result.message
        assert "test-key" not in result.message
    openai.close()


def _check_from_env_requires_key() -> None:
    import os

    prev = os.environ.pop("OPENAI_API_KEY", None)
    try:
        try:
            OpenAICompatibleProvider.from_env()
            raise AssertionError("expected ValueError")
        except ValueError as exc:
            assert "OPENAI_API_KEY" in str(exc)
    finally:
        if prev is not None:
            os.environ["OPENAI_API_KEY"] = prev


def _check_gateway_registers_openai_name() -> None:
    import os

    from control_plane.gateway import _provider_for

    prev = os.environ.pop("OPENAI_API_KEY", None)
    try:
        try:
            _provider_for("openai")
            raise AssertionError("expected missing OPENAI_API_KEY")
        except ValueError as exc:
            assert "OPENAI_API_KEY" in str(exc)
    finally:
        if prev is not None:
            os.environ["OPENAI_API_KEY"] = prev


def main() -> None:
    _check_success_and_contract_shape()
    _check_error_mapping()
    _check_from_env_requires_key()
    _check_gateway_registers_openai_name()
    print("openai-compatible adapter OK")


if __name__ == "__main__":
    main()
