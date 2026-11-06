import json
from dataclasses import replace

import httpx
import pytest

from quilr_gateway.app import create_app

VIEWER = {"Authorization": "Bearer viewer-key"}
ADMIN = {"Authorization": "Bearer admin-key"}


def rpc(method="tools/list", params=None, request_id=1):
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}


async def test_auth_and_role_enforcement_prevents_downstream_call(gateway):
    client, downstream = gateway
    assert (await client.post("/mcp", json=rpc())).status_code == 401
    denied = await client.post(
        "/mcp", headers=VIEWER, json=rpc("tools/call", {"name": "admin_reset_key"}, "test-id")
    )
    assert denied.json() == {
        "jsonrpc": "2.0",
        "id": "test-id",
        "error": {
            "code": -32001,
            "message": "Unauthorized Tool Call",
        },
    }
    assert downstream.state.calls == 0
    allowed = await client.post(
        "/mcp", headers=ADMIN, json=rpc("tools/call", {"name": "admin_reset_key"})
    )
    assert "result" in allowed.json()
    assert downstream.state.calls == 1
    assert (
        "result"
        in (
            await client.post(
                "/mcp", headers=VIEWER, json=rpc("tools/call", {"name": "public_status"})
            )
        ).json()
    )


async def test_tool_list_is_transparent_and_ids_preserved(gateway):
    client, _ = gateway
    for key in (VIEWER, ADMIN):
        response = await client.post("/mcp", headers=key, json=rpc(request_id="abc"))
        assert response.json()["id"] == "abc"
        assert [t["name"] for t in response.json()["result"]["tools"]] == [
            "public_status",
            "admin_reset_key",
        ]


@pytest.mark.parametrize(
    "payload,code",
    [
        ([], -32600),
        ({}, -32600),
        ({"jsonrpc": "1.0", "id": 1, "method": "tools/list"}, -32600),
        (rpc(request_id=True), -32600),
        (rpc("bogus"), -32601),
        (rpc("tools/call", {"name": 42}), -32602),
        ({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": []}, -32602),
    ],
)
async def test_invalid_rpc(gateway, payload, code):
    client, _ = gateway
    response = await client.post("/mcp", headers=VIEWER, json=payload)
    assert response.json()["error"]["code"] == code


async def test_malformed_json_and_oversized_body(gateway):
    client, _ = gateway
    headers = {**VIEWER, "Content-Type": "application/json"}
    for body in ("{", '{"x":NaN}', '"' + "a" * 65536 + '"'):
        response = await client.post("/mcp", headers=headers, content=body)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == -32700


async def test_unauthorized_notifications_are_not_forwarded(gateway):
    client, downstream = gateway
    payload = rpc("tools/call", {"name": "admin_reset_key"})
    del payload["id"]
    response = await client.post("/mcp", headers=VIEWER, json=payload)
    assert response.status_code == 202
    assert not response.content
    assert downstream.state.calls == 0


async def test_completion_redacted_and_primary_selected(gateway):
    client, _ = gateway
    response = await client.post("/v1/completions", headers=VIEWER, json={"prompt": "Hello"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"].count("[REDACTED]") == 3
    assert "alex@" not in body["text"] and "123-45" not in body["text"]
    assert body["provider"] == "primary"
    assert body["reserved_tokens"] == 5 + 32 + 256


async def test_fallback_and_sanitized_errors(gateway):
    client, _ = gateway
    response = await client.post("/v1/completions", headers=VIEWER, json={"prompt": "[force-429]"})
    assert response.status_code == 200
    assert response.json()["provider"] == "backup"
    response = await client.post(
        "/v1/completions", headers=VIEWER, json={"prompt": "[force-error]"}
    )
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "upstream_unavailable"
    assert "PRIVATE" not in response.text and "Traceback" not in response.text


async def test_streaming_sse_redacted_and_done(gateway):
    client, _ = gateway
    response = await client.post(
        "/v1/completions", headers=VIEWER, json={"prompt": "Hello", "stream": True}
    )
    assert response.headers["content-type"].startswith("text/event-stream")
    frames = response.text.strip().split("\n\n")
    assert frames[-1].startswith("event: done")
    data = [json.loads(f.split("data: ", 1)[1]) for f in frames[:-1]]
    text = "".join(item["text"] for item in data)
    assert text.count("[REDACTED]") == 3
    assert "alex@example.com" not in response.text


async def test_sse_error_has_no_success_event(gateway):
    client, _ = gateway
    response = await client.post(
        "/v1/completions",
        headers=VIEWER,
        json={
            "prompt": "[force-error]",
            "stream": True,
        },
    )
    assert "event: error" in response.text
    assert "event: done" not in response.text
    assert "PRIVATE" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt": ""},
        {"prompt": 123},
        {"prompt": "hi", "max_tokens": True},
        {"prompt": "hi", "extra": "bad"},
        {"prompt": "hi", "max_tokens": 0},
        {"prompt": "hi", "stream": "true"},
    ],
)
async def test_completion_strict_inputs(gateway, payload):
    client, _ = gateway
    assert (await client.post("/v1/completions", headers=VIEWER, json=payload)).status_code == 400


async def test_rate_limit_shared_by_tenant_and_isolated_between_tenants(settings):
    settings = replace(settings, tokens_per_minute=50)
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(
            200, headers={"Content-Type": "text/event-stream"}, content="data: [DONE]\n\n"
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as upstream:
        app = create_app(settings, upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                data = {"prompt": "x", "max_tokens": 10}
                assert (
                    await client.post("/v1/completions", headers=VIEWER, json=data)
                ).status_code == 200
                denied = await client.post("/v1/completions", headers=ADMIN, json=data)
                assert denied.status_code == 429 and int(denied.headers["retry-after"]) > 0
                other = await client.post(
                    "/v1/completions",
                    headers={
                        "Authorization": "Bearer other-key",
                    },
                    json=data,
                )
                assert other.status_code == 200
                assert len(calls) == 2


async def test_proxy_does_not_forward_caller_credentials(settings):
    calls = []

    def responder(request):
        calls.append(request)
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"tools": []}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as upstream:
        app = create_app(replace(settings, mcp_token="service-token"), upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://t"
            ) as c:
                assert (await c.post("/mcp", headers=VIEWER, json=rpc())).status_code == 200
    assert calls[0].headers["authorization"] == "Bearer service-token"
