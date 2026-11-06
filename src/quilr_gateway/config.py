"""Explicit runtime configuration; demo credentials are confined to the demo runner."""

import json
import os
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Identity:
    tenant: str
    role: str


@dataclass(frozen=True)
class ProviderConfig:
    name: str
    url: str
    model: str
    api_key: str = field(default="", repr=False)


@dataclass(frozen=True)
class Settings:
    identities: dict[str, Identity] = field(repr=False)
    primary: ProviderConfig
    backup: ProviderConfig
    mcp_url: str = "http://127.0.0.1:9001/mcp"
    database: str = "data/gateway.sqlite3"
    tokens_per_minute: int = 50_000
    provider_timeout: float = 3.0
    request_timeout: float = 30.0
    mcp_token: str = field(default="", repr=False)

    @classmethod
    def from_env(cls) -> "Settings":
        raw = json.loads(os.environ["GATEWAY_TOKENS"])
        if not isinstance(raw, dict) or not raw:
            raise ValueError("GATEWAY_TOKENS must be a nonempty token-to-identity map")
        identities = {}
        for token, item in raw.items():
            if not token or item.get("role") not in {"admin", "viewer"} or not item.get("tenant"):
                raise ValueError("Invalid gateway identity")
            identities[token] = Identity(tenant=item["tenant"], role=item["role"])
        return cls(
            identities=identities,
            primary=ProviderConfig(
                "primary",
                os.environ["PRIMARY_URL"],
                os.environ.get("PRIMARY_MODEL", "primary"),
                os.environ.get("PRIMARY_API_KEY", ""),
            ),
            backup=ProviderConfig(
                "backup",
                os.environ["BACKUP_URL"],
                os.environ.get("BACKUP_MODEL", "backup"),
                os.environ.get("BACKUP_API_KEY", ""),
            ),
            mcp_url=os.environ.get("MCP_URL", "http://127.0.0.1:9001/mcp"),
            mcp_token=os.environ.get("MCP_TOKEN", ""),
            database=os.environ.get("DATABASE_PATH", "data/gateway.sqlite3"),
        )
