"""M1a fake + HTTP stub self-check. Run: python -m tests.check_m1a_stub"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from control_plane.api import app
from control_plane.providers.base import ProviderFailure, ProviderRequest, ProviderSuccess
from control_plane.providers.fake import FakeProvider
from control_plane.schemas import ErrorCategory


def _check_fake() -> None:
    fake = FakeProvider()
    ok = fake.complete(
        ProviderRequest(
            model="fake-support",
            messages=[{"role": "user", "content": "escalate: billing dispute"}],
            timeout_ms=100,
        )
    )
    assert isinstance(ok, ProviderSuccess)
    body = json.loads(ok.content)
    assert body["classification"] == "billing"
    assert body["citation_ids"] == ["pol-billing"]
    assert ok.raw_model == "fake-support"
    assert ok.latency_ms == 1.0

    # Same input → same content (deterministic).
    again = fake.complete(
        ProviderRequest(
            model="fake-support",
            messages=[{"role": "user", "content": "escalate: billing dispute"}],
            timeout_ms=100,
        )
    )
    assert isinstance(again, ProviderSuccess)
    assert again.content == ok.content

    fail = fake.complete(
        ProviderRequest(
            model="fake-support",
            messages=[{"role": "user", "content": "[[fake:timeout]] please help"}],
            timeout_ms=100,
        )
    )
    assert isinstance(fail, ProviderFailure)
    assert fail.category == ErrorCategory.TIMEOUT


def _check_http() -> None:
    client = TestClient(app)
    headers = {"Authorization": "Bearer sk-demo-tenant-a"}

    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["config_version"] == "support-v1"

    resp = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "support-default",
            "messages": [{"role": "user", "content": "customer access locked"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["object"] == "chat.completion"
    assert data["model"] == "support-default"
    content = json.loads(data["choices"][0]["message"]["content"])
    assert content["classification"] == "access"
    assert data["usage"]["total_tokens"] == data["usage"]["prompt_tokens"] + data["usage"]["completion_tokens"]

    bad_alias = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]},
    )
    assert bad_alias.status_code == 400
    assert bad_alias.json()["error"] == "permanent-client-error"

    injected = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "support-default",
            "messages": [{"role": "user", "content": "[[fake:unavailable]] outage"}],
        },
    )
    assert injected.status_code == 503
    assert injected.json()["error"] == "unavailable"
    assert injected.json()["retryable"] is True

    streamed = client.post(
        "/v1/chat/completions",
        headers=headers,
        json={
            "model": "support-default",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert streamed.status_code == 400
    assert streamed.json()["error"] == "permanent-client-error"


def main() -> None:
    _check_fake()
    _check_http()
    print("M1a fake + HTTP stub OK")


if __name__ == "__main__":
    main()
