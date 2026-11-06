"""OpenAI-compatible HTTP/SSE adapter and bounded, cancellation-safe failover."""

import asyncio
import json
from collections.abc import AsyncIterator

import httpx

from quilr_gateway.config import ProviderConfig
from quilr_gateway.schemas import CompletionInput


class ProviderFailure(Exception):
    def __init__(self, retryable: bool = False):
        self.retryable = retryable


async def sse_data(response: httpx.Response) -> AsyncIterator[str]:
    """Parse arbitrary UTF-8/network boundaries with a 64 KiB event limit."""
    buffer = b""
    data: list[str] = []
    size = 0
    async for chunk in response.aiter_bytes():
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            size += len(line) + 1
            if size > 65536:
                raise ProviderFailure()
            line = line.rstrip(b"\r").decode("utf-8")
            if not line:
                if data:
                    yield "\n".join(data)
                data, size = [], 0
            elif line.startswith("data:"):
                data.append(line[5:].removeprefix(" "))
        if size + len(buffer) > 65536:
            raise ProviderFailure()
    # SSE events require a blank-line terminator. A partial last event is a failure.
    if buffer or data:
        raise ProviderFailure()


class HTTPProvider:
    def __init__(self, client: httpx.AsyncClient, config: ProviderConfig):
        self.client, self.config = client, config

    async def stream(self, request: CompletionInput) -> AsyncIterator[str]:
        headers = {"Accept": "text/event-stream"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        try:
            async with self.client.stream(
                "POST",
                self.config.url,
                headers=headers,
                json={
                    "model": self.config.model,
                    "messages": [{"role": "user", "content": request.prompt}],
                    "max_tokens": request.max_tokens,
                    "stream": True,
                },
            ) as response:
                if response.status_code != 200:
                    raise ProviderFailure(retryable=response.status_code == 429)
                if "text/event-stream" not in response.headers.get("content-type", ""):
                    raise ProviderFailure()
                async for data in sse_data(response):
                    if data == "[DONE]":
                        return
                    event = json.loads(data)
                    if "error" in event:
                        raise ProviderFailure()
                    choices = event.get("choices", [])
                    if not choices:  # Usage-only events.
                        continue
                    text = choices[0].get("delta", {}).get("content")
                    if text is not None:
                        if not isinstance(text, str) or len(text) > 16384:
                            raise ProviderFailure()
                        if text:
                            yield text
                raise ProviderFailure()  # Truncated stream without [DONE].
        except httpx.TimeoutException:
            raise ProviderFailure(retryable=True) from None
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError, IndexError):
            raise ProviderFailure() from None


class Router:
    def __init__(self, primary: HTTPProvider, backup: HTTPProvider, timeout: float = 3.0):
        self.providers, self.timeout = (primary, backup), timeout

    async def stream(self, request: CompletionInput) -> AsyncIterator[tuple[str, str]]:
        for index, provider in enumerate(self.providers):
            iterator = provider.stream(request)
            committed = False
            try:
                while True:
                    try:
                        async with asyncio.timeout(self.timeout):
                            text = await anext(iterator)
                    except TimeoutError:
                        raise ProviderFailure(retryable=True) from None
                    except StopAsyncIteration:
                        return
                    committed = True
                    yield provider.config.name, text
            except ProviderFailure as exc:
                # Switching after output began would splice two independent answers.
                if index == 0 and exc.retryable and not committed:
                    continue
                raise
            finally:
                await iterator.aclose()
