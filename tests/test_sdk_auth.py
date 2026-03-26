from __future__ import annotations

import httpx
import pytest

from synapse.sdk.client import SynapseClient


def test_sdk_attaches_bearer_and_project_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        captured["project"] = request.headers.get("X-Synapse-Project-Id", "")
        return httpx.Response(200, json=[])

    client = SynapseClient(
        base_url="http://testserver",
        bearer_token="token-123",
        project_id="project-1",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.list_tools()
    finally:
        client.close()

    assert captured["authorization"] == "Bearer token-123"
    assert captured["project"] == "project-1"


def test_sdk_attaches_api_key_headers() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization", "")
        captured["api_key"] = request.headers.get("X-API-Key", "")
        return httpx.Response(200, json=[])

    client = SynapseClient(
        base_url="http://testserver",
        api_key="key-abc",
        transport=httpx.MockTransport(handler),
    )

    try:
        client.list_tools()
    finally:
        client.close()

    assert captured["api_key"] == "key-abc"
    assert captured["authorization"] == "Bearer key-abc"


def test_sdk_refreshes_bearer_token_on_401() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.headers.get("Authorization", ""))
        if len(calls) == 1:
            return httpx.Response(401, json={"detail": "token expired"})
        return httpx.Response(200, json=[])

    refreshed = {"count": 0}

    def refresh() -> str:
        refreshed["count"] += 1
        return "token-new"

    client = SynapseClient(
        base_url="http://testserver",
        bearer_token="token-old",
        token_refresh_callback=refresh,
        transport=httpx.MockTransport(handler),
    )

    try:
        client.list_tools()
    finally:
        client.close()

    assert refreshed["count"] == 1
    assert calls == ["Bearer token-old", "Bearer token-new"]


def test_sdk_auth_failure_message_is_improved() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "Run is outside the caller project scope."})

    client = SynapseClient(
        base_url="http://testserver",
        bearer_token="token-123",
        project_id="project-a",
        transport=httpx.MockTransport(handler),
    )

    try:
        with pytest.raises(PermissionError) as exc_info:
            client.list_tools()
    finally:
        client.close()

    message = str(exc_info.value)
    assert "Authorization failed" in message
    assert "project-a" in message
    assert "outside the caller project scope" in message
