"""Local-only deterministic downstream fixtures. Never mount these in production."""

import asyncio
import json

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from quilr_gateway.app import rpc_error


def create_mock_app():
    async def mcp(request: Request):
        message = await request.json()
        method, params = message["method"], message.get("params", {})
        request_id = message.get("id")
        if "id" not in message:
            return Response(status_code=202)
        if method == "initialize":
            result = {
                "protocolVersion": "2025-11-25",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock-downstream", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {
                "tools": [
                    {
                        "name": name,
                        "description": description,
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "additionalProperties": False,
                        },
                    }
                    for name, description in [
                        ("public_status", "Read mock service status"),
                        ("admin_reset_key", "Simulate administrative key rotation"),
                    ]
                ]
            }
        elif method == "tools/call":
            if params.get("name") not in {"public_status", "admin_reset_key"}:
                return rpc_error(request_id, -32602, "Unknown tool")
            request.app.state.calls += 1
            result = {
                "content": [{"type": "text", "text": "Mock operation completed"}],
                "isError": False,
            }
        elif method == "ping":
            result = {}
        else:
            return rpc_error(request_id, -32601, "Method not found")
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    async def provider(request: Request):
        body = await request.json()
        prompt = body["messages"][0]["content"]
        primary = request.path_params["provider"] == "primary"
        if primary and "[force-429]" in prompt:
            return JSONResponse({"error": "Synthetic throttling"}, status_code=429)
        if primary and "[force-timeout]" in prompt:
            await asyncio.sleep(3.5)
        if "[force-error]" in prompt:
            return JSONResponse({"error": "PRIVATE upstream diagnostic"}, status_code=500)
        text = (
            "Demo reply: email alex@example.com, SSN 123-45-6789, "
            "card 4111 1111 1111 1111. All sensitive values must be hidden."
        )

        async def chunks():
            # Deliberately split inside every kind of PII.
            for offset in range(0, len(text), 4):
                event = {"choices": [{"delta": {"content": text[offset : offset + 4]}}]}
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(0.005)
            yield "data: [DONE]\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    app = Starlette(
        routes=[
            Route("/mcp", mcp, methods=["POST"]),
            Route("/{provider}/v1/chat/completions", provider, methods=["POST"]),
        ]
    )
    app.state.calls = 0
    return app
