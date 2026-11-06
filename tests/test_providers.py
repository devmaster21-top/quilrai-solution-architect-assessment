import asyncio
import json
from contextlib import aclosing

import httpx
import pytest

from quilr_gateway.config import ProviderConfig
from quilr_gateway.providers import HTTPProvider, ProviderFailure, Router, sse_data
from quilr_gateway.schemas import CompletionInput


class FakeProvider:
    def __init__(self, name, failure=None, delay=0, fail_after=False):
        self.config = ProviderConfig(name, "http://mock", "mock")
        self.failure, self.delay, self.fail_after = failure, delay, fail_after
        self.calls, self.closed = 0, False

    async def stream(self, request):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
            if self.failure and not self.fail_after:
                raise self.failure
            yield "Hello "
            if self.fail_after:
                raise ProviderFailure(retryable=True)
            yield "world!"
        finally:
            self.closed = True


@pytest.mark.parametrize("mode", ["429", "timeout"])
async def test_failover_before_first_token(mode):
    primary = FakeProvider(
        "primary",
        failure=ProviderFailure(True) if mode == "429" else None,
        delay=0.05 if mode == "timeout" else 0,
    )
    backup = FakeProvider("backup")
    router = Router(primary, backup, timeout=0.01)
    result = [part async for part in router.stream(CompletionInput(prompt="hi"))]
    assert result == [("backup", "Hello "), ("backup", "world!")]
    assert primary.closed and backup.closed


async def test_no_fallback_after_partial_output():
    primary, backup = FakeProvider("primary", fail_after=True), FakeProvider("backup")
    with pytest.raises(ProviderFailure):
        async for _ in Router(primary, backup).stream(CompletionInput(prompt="hi")):
            pass
    assert backup.calls == 0 and primary.closed


async def test_no_fallback_for_nonretryable_error():
    primary, backup = FakeProvider("primary", failure=ProviderFailure()), FakeProvider("backup")
    with pytest.raises(ProviderFailure):
        async for _ in Router(primary, backup).stream(CompletionInput(prompt="hi")):
            pass
    assert backup.calls == 0


async def test_both_providers_fail():
    primary = FakeProvider("primary", failure=ProviderFailure(True))
    backup = FakeProvider("backup", failure=ProviderFailure(True))
    with pytest.raises(ProviderFailure):
        async for _ in Router(primary, backup).stream(CompletionInput(prompt="hi")):
            pass
    assert primary.closed and backup.closed


async def test_cancellation_closes_current_upstream():
    primary, backup = FakeProvider("primary"), FakeProvider("backup")
    async with aclosing(Router(primary, backup).stream(CompletionInput(prompt="hi"))) as stream:
        assert await anext(stream) == ("primary", "Hello ")
    assert primary.closed and backup.calls == 0


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


async def test_sse_parser_utf8_and_every_network_boundary():
    payload = ': heartbeat\r\ndata: {"text":"café"}\r\n\r\ndata: [DONE]\n\n'.encode()
    for split in range(len(payload) + 1):
        response = httpx.Response(200, stream=ByteStream([payload[:split], payload[split:]]))
        assert [item async for item in sse_data(response)] == ['{"text":"café"}', "[DONE]"]


@pytest.mark.parametrize("data", [b"data: missing terminator", b"data: " + b"a" * 70000])
async def test_sse_parser_rejects_truncated_and_oversized_events(data):
    response = httpx.Response(200, stream=ByteStream([data]))
    with pytest.raises(ProviderFailure):
        async for _ in sse_data(response):
            pass


@pytest.mark.parametrize("status,retryable", [(429, True), (401, False), (500, False)])
async def test_http_provider_status_mapping(status, retryable):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "private"}))
    ) as client:
        provider = HTTPProvider(client, ProviderConfig("p", "http://mock", "m"))
        with pytest.raises(ProviderFailure) as error:
            async for _ in provider.stream(CompletionInput(prompt="hi")):
                pass
        assert error.value.retryable == retryable
        assert "private" not in str(error.value)


async def test_provider_reads_sse_deltas_and_done():
    payload = json.dumps({"choices": [{"delta": {"content": "Hello"}}]})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                headers={"Content-Type": "text/event-stream"},
                content=f"data: {payload}\n\ndata: [DONE]\n\n",
            )
        )
    ) as client:
        provider = HTTPProvider(client, ProviderConfig("p", "http://mock", "m"))
        assert [t async for t in provider.stream(CompletionInput(prompt="hi"))] == ["Hello"]
