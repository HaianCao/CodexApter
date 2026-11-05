from unittest.mock import patch

import pytest

from codex_adapter.catalog import fetch_models
from codex_adapter.config import (
    Config,
    Provider,
    load,
    save,
    validate_auth_mode,
    validate_client_tool_search,
)
from codex_adapter.errors import ConfigError
from codex_adapter.launcher import _gateway
from codex_adapter.web import public_config


@pytest.mark.parametrize("protocol", ["responses", "chat", "messages"])
@pytest.mark.parametrize("auth", ["bearer", "api_key"])
def test_authentication_is_independent_of_protocol_and_survives_reload(tmp_path, protocol, auth):
    pytest.importorskip("protoverta")
    provider = Provider("demo", "Demo", "https://example.test/v1", protocol=protocol, execution_mode="gateway", auth_mode=auth)
    config = Config(providers={"demo": provider}, path=tmp_path / "config.toml")
    save(config)
    restored = load(config.path)
    assert restored.providers["demo"].auth_mode == auth
    assert public_config(restored)["providers"][0]["auth_mode"] == auth
    with patch("codex_adapter.launcher.resolve_token", return_value="fixture"):
        gateway, _ = _gateway(restored, restored.providers["demo"])
    assert gateway.auth_style == ("x-api-key" if auth == "api_key" else "bearer")


def test_old_messages_auth_is_preserved_without_changing_new_provider_default(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[providers.demo]\nbase_url="https://example.test/v1"\nprotocol="messages"\nexecution_mode="gateway"\n', encoding="utf-8")
    assert load(path).providers["demo"].auth_mode == "api_key"
    assert Provider("new", "New", "https://example.test/v1").auth_mode == "bearer"


@pytest.mark.parametrize("value,mode", [([], "gateway"), ({}, "gateway"), ("other", "gateway"), ("api_key", "direct")])
def test_invalid_or_unusable_auth_is_rejected(value, mode):
    with pytest.raises(ConfigError):
        validate_auth_mode(value, mode)


def test_client_search_policy_persists_and_reaches_gateway(tmp_path):
    pytest.importorskip("protoverta")
    provider = Provider("demo", "Demo", "https://example.test/v1", protocol="chat", execution_mode="gateway", client_tool_search="function")
    config = Config(providers={"demo": provider}, path=tmp_path / "config.toml")
    save(config)
    restored = load(config.path)
    assert restored.providers["demo"].client_tool_search == "function"
    with patch("codex_adapter.launcher.resolve_token", return_value="fixture"):
        gateway, _ = _gateway(restored, restored.providers["demo"])
    assert gateway.client_tool_search == "function"
    with pytest.raises(ConfigError):
        validate_client_tool_search("function", "responses", "direct")


@pytest.mark.parametrize("auth", ["bearer", "api_key"])
def test_discovery_uses_selected_auth(tmp_path, auth):
    pytest.importorskip("protoverta.discovery")
    from protoverta.discovery import ModelInfo

    provider = Provider("demo", "Demo", "https://example.test/v1", execution_mode="gateway", auth_mode=auth)
    provider.discovery.enabled = True
    with patch("codex_adapter.catalog.resolve_token", return_value="fixture"), patch("protoverta.discovery.fetch_catalog", return_value=(ModelInfo("m", "m"),)) as opened:
        assert fetch_models(Config(), provider, cache=tmp_path / "cache.json") == ["m"]
    headers = {key.lower(): value for key, value in opened.call_args.args[1].items()}
    assert headers.get("authorization") == ("Bearer fixture" if auth == "bearer" else None)
    assert headers.get("x-api-key") == ("fixture" if auth == "api_key" else None)
