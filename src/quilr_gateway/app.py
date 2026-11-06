"""Authenticated HTTP/JSON-RPC MCP proxy and guarded LLM completion endpoint."""

import asyncio
import hmac
import json
import logging
from contextlib import aclosing, asynccontextmanager
from uuid import uuid4

import httpx
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from quilr_gateway.config import Identity, Settings
from quilr_gateway.limiter import RateLimited, TokenLimiter
from quilr_gateway.providers import HTTPProvider, ProviderFailure, Router
from quilr_gateway.redaction import StreamRedactor
from quilr_gateway.schemas import CompletionInput

logger = logging.getLogger(__name__)
MAX_BODY = 65536


class BadRequest(Exception):
    pass


async def read_json(request: Request):
    if request.headers.get("content-type", "").split(";")[0] != "application/json":
        raise BadRequest()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY:
            raise BadRequest()
    try:
        return json.loads(body, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError):
        raise BadRequest() from None


def identity(request: Request, settings: Settings) -> Identity | None:
    parts = request.headers.get("authorization", "").split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    for token, user in settings.identities.items():
        if hmac.compare_digest(parts[1].encode(), token.encode()):
            return user
    return None


def rpc_error(request_id, code: int, message: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=status,
    )


def gateway_error(code: str, message: str, request_id: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message, "request_id": request_id}},
        status_code=status,
    )


def sse(event: str, value: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(value, allow_nan=False)}\n\n"


def create_app(settings: Settings | None = None, client: httpx.AsyncClient | None = None):
    settings = settings or Settings.from_env()
    limiter = TokenLimiter(settings.database, settings.tokens_per_minute)

    @asynccontextmanager
    async def lifespan(app):
        if client is not None:
            app.state.client = client
            yield
        else:
            async with httpx.AsyncClient(
                timeout=5, follow_redirects=False, trust_env=False
            ) as owned:
                app.state.client = owned
                yield

    async def health(request):
        return JSONResponse({"status": "ok"})

    async def mcp_proxy(request: Request):
        user = identity(request, settings)
        if user is None:
            response = rpc_error(None, -32001, "Authentication required", 401)
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        try:
            payload = await read_json(request)
        except BadRequest:
            return rpc_error(None, -32700, "Parse error", 400)
        if not isinstance(payload, dict):
            return rpc_error(None, -32600, "Invalid Request", 400)
        request_id = payload.get("id")
        if (
            payload.get("jsonrpc") != "2.0"
            or not isinstance(payload.get("method"), str)
            or (
                "id" in payload
                and (
                    isinstance(request_id, bool)
                    or not isinstance(request_id, (str, int, type(None)))
                )
            )
        ):
            return rpc_error(None, -32600, "Invalid Request", 400)
        notification = "id" not in payload
        method, params = payload["method"], payload.get("params", {})
        if not isinstance(params, dict):
            return (
                Response(status_code=202)
                if notification
                else rpc_error(request_id, -32602, "Invalid params")
            )
        if method == "tools/call":
            name = params.get("name")
            if not isinstance(name, str) or not name:
                return (
                    Response(status_code=202)
                    if notification
                    else rpc_error(request_id, -32602, "Invalid params")
                )
            if name.startswith("admin_") and user.role != "admin":
                return (
                    Response(status_code=202)
                    if notification
                    else rpc_error(request_id, -32001, "Unauthorized Tool Call")
                )
        elif method not in {"tools/list", "initialize", "notifications/initialized", "ping"}:
            return (
                Response(status_code=202)
                if notification
                else rpc_error(request_id, -32601, "Method not found")
            )
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if settings.mcp_token:
            headers["Authorization"] = f"Bearer {settings.mcp_token}"
        # Deliberately do not forward the caller's bearer token or arbitrary headers.
        try:
            async with request.app.state.client.stream(
                "POST", settings.mcp_url, json=payload, headers=headers
            ) as downstream:
                body = bytearray()
                async for chunk in downstream.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_BODY:
                        raise ValueError()
                if downstream.status_code not in {200, 202, 204}:
                    raise ValueError()
                if notification:
                    return Response(status_code=202)
                data = json.loads(body)
                if (
                    not isinstance(data, dict)
                    or data.get("jsonrpc") != "2.0"
                    or data.get("id") != request_id
                    or (("result" in data) == ("error" in data))
                ):
                    raise ValueError()
                return JSONResponse(data)
        except (httpx.HTTPError, ValueError):
            return (
                Response(status_code=202)
                if notification
                else rpc_error(request_id, -32603, "Downstream unavailable", 502)
            )

    async def completion(request: Request):
        request_id = uuid4().hex
        user = identity(request, settings)
        if user is None:
            response = gateway_error("unauthorized", "Authentication required", request_id, 401)
            response.headers["WWW-Authenticate"] = "Bearer"
            return response
        try:
            values = CompletionInput.model_validate(await read_json(request))
        except (BadRequest, ValidationError):
            return gateway_error("invalid_request", "Invalid completion request", request_id, 400)
        # UTF-8 bytes upper-bound common byte-level tokenizer prompt counts. Fixed
        # overhead covers the single user message envelope. Reserve output upfront.
        budget = len(values.prompt.encode("utf-8")) + 32 + values.max_tokens
        try:
            await limiter.reserve(user.tenant, budget)
        except RateLimited as exc:
            response = gateway_error(
                "rate_limited", "Tenant token budget exceeded", request_id, 429
            )
            response.headers["Retry-After"] = str(exc.retry_after)
            return response
        except Exception:
            logger.error("Rate limiter unavailable request_id=%s", request_id)
            return gateway_error("gateway_unavailable", "Gateway unavailable", request_id, 503)
        http_client = request.app.state.client
        router = Router(
            HTTPProvider(http_client, settings.primary),
            HTTPProvider(http_client, settings.backup),
            settings.provider_timeout,
        )

        async def guarded():
            redactor = StreamRedactor()
            output_bytes = 0
            # Also cap total request duration: an upstream sending endless deltas
            # must not hold a connection or memory indefinitely.
            async with asyncio.timeout(settings.request_timeout):
                async with aclosing(router.stream(values)) as stream:
                    async for provider, text in stream:
                        output_bytes += len(text.encode("utf-8"))
                        if output_bytes > values.max_tokens * 128:
                            raise ProviderFailure()
                        safe = redactor.feed(text)
                        if safe:
                            yield provider, safe
                tail = redactor.finish()
                if tail:
                    yield provider, tail

        async def events():
            try:
                async with aclosing(guarded()) as stream:
                    async for provider, text in stream:
                        yield sse("delta", {"text": text, "provider": provider})
                yield sse("done", {"request_id": request_id, "reserved_tokens": budget})
            except (ProviderFailure, TimeoutError):
                # Once SSE starts HTTP status cannot change. End with a typed error,
                # never flush an incomplete PII candidate or report successful done.
                yield sse(
                    "error",
                    {
                        "error": {
                            "code": "upstream_unavailable",
                            "message": "Model provider unavailable",
                            "request_id": request_id,
                        }
                    },
                )

        if values.stream:
            return StreamingResponse(
                events(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-store",
                    "X-Accel-Buffering": "no",
                    "X-Request-ID": request_id,
                },
            )
        try:
            chunks, provider_name = [], None
            async with aclosing(guarded()) as stream:
                async for selected_provider, text in stream:
                    provider_name = selected_provider
                    chunks.append(text)
            return JSONResponse(
                {
                    "text": "".join(chunks),
                    "provider": provider_name,
                    "reserved_tokens": budget,
                    "request_id": request_id,
                }
            )
        except (ProviderFailure, TimeoutError):
            return gateway_error(
                "upstream_unavailable", "Model provider unavailable", request_id, 502
            )

    async def unhandled(request, exc):
        request_id = uuid4().hex
        logger.error("Unhandled gateway failure request_id=%s", request_id)
        return gateway_error("internal_error", "Gateway unavailable", request_id, 500)

    app = Starlette(
        routes=[
            Route("/health", health),
            Route("/mcp", mcp_proxy, methods=["POST"]),
            Route("/v1/completions", completion, methods=["POST"]),
        ],
        lifespan=lifespan,
        exception_handlers={Exception: unhandled},
    )
    app.state.limiter = limiter
    return app
