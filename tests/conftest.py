import httpx
import pytest

from quilr_gateway.app import create_app
from quilr_gateway.config import Identity, ProviderConfig, Settings
from quilr_gateway.mocks import create_mock_app


@pytest.fixture
def settings(tmp_path):
    return Settings(
        identities={
            "viewer-key": Identity("tenant-a", "viewer"),
            "admin-key": Identity("tenant-a", "admin"),
            "other-key": Identity("tenant-b", "viewer"),
        },
        primary=ProviderConfig("primary", "http://mock/primary/v1/chat/completions", "model-a"),
        backup=ProviderConfig("backup", "http://mock/backup/v1/chat/completions", "model-b"),
        mcp_url="http://mock/mcp",
        database=str(tmp_path / "gateway.sqlite3"),
    )


@pytest.fixture
async def gateway(settings):
    downstream = create_mock_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=downstream)) as upstream:
        app = create_app(settings, upstream)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://gateway"
            ) as client:
                yield client, downstream
