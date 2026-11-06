"""Start both isolated mock upstreams and the gateway with one command."""

import asyncio
import logging

import uvicorn

from quilr_gateway.app import create_app
from quilr_gateway.config import Identity, ProviderConfig, Settings
from quilr_gateway.mocks import create_mock_app


async def run():
    settings = Settings(
        identities={
            "demo-viewer-token": Identity("demo-tenant", "viewer"),
            "demo-admin-token": Identity("demo-tenant", "admin"),
            "demo-other-token": Identity("other-tenant", "viewer"),
        },
        primary=ProviderConfig(
            "primary", "http://127.0.0.1:9001/primary/v1/chat/completions", "demo"
        ),
        backup=ProviderConfig("backup", "http://127.0.0.1:9001/backup/v1/chat/completions", "demo"),
    )
    servers = [
        uvicorn.Server(uvicorn.Config(create_mock_app(), host="127.0.0.1", port=9001)),
        uvicorn.Server(uvicorn.Config(create_app(settings), host="127.0.0.1", port=8000)),
    ]
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for server in servers:
            server.should_exit = True
        await asyncio.gather(*tasks)


def main():
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
