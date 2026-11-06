import pytest

from quilr_gateway.config import Settings


def test_env_configuration(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKENS", '{"secret":{"tenant":"a","role":"admin"}}')
    monkeypatch.setenv("PRIMARY_URL", "https://primary.example/v1/chat/completions")
    monkeypatch.setenv("BACKUP_URL", "https://backup.example/v1/chat/completions")
    monkeypatch.setenv("PRIMARY_API_KEY", "provider-secret")
    settings = Settings.from_env()
    assert settings.identities["secret"].role == "admin"
    assert "provider-secret" not in repr(settings)
    assert "secret" not in repr(settings)


@pytest.mark.parametrize(
    "value",
    ["{}", "[]", '{"x":{"role":"root","tenant":"a"}}', '{"":{"role":"admin","tenant":"a"}}'],
)
def test_invalid_identity_configuration(monkeypatch, value):
    monkeypatch.setenv("GATEWAY_TOKENS", value)
    with pytest.raises(ValueError):
        Settings.from_env()
