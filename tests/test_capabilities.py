import sys
import types
from unittest.mock import Mock, patch

import pytest

from codex_adapter.config import Config, Provider, load, save
from codex_adapter.errors import ConfigError, LaunchError
from codex_adapter.launcher import _gateway, preflight
from codex_adapter.web import public_config


def test_registry_settings_persist_and_require_gateway(tmp_path):
    provider = Provider("demo", "Demo", "https://example.test/v1", execution_mode="gateway", capabilities_file="checks.json", require_verified_capabilities=True)
    config = Config(providers={"demo": provider}, path=tmp_path / "config.toml")
    save(config)
    restored = load(config.path)
    assert restored.providers["demo"] == provider
    assert public_config(restored)["providers"][0]["require_verified_capabilities"] is True
    provider.execution_mode = "direct"
    with pytest.raises(ConfigError, match="gateway"):
        save(config)
    provider.execution_mode = "gateway"
    provider.capabilities_file = ""
    with pytest.raises(ConfigError, match="registry"):
        save(config)
    provider.require_verified_capabilities = "false"
    with pytest.raises(ConfigError, match="boolean"):
        save(config)


def test_registry_path_is_resolved_relative_to_configuration_not_working_directory(tmp_path):
    provider = Provider("demo", "Demo", "https://example.test/v1", execution_mode="gateway", capabilities_file="checks.json", require_verified_capabilities=True)
    config = Config(providers={"demo": provider}, path=tmp_path / "config.toml")
    core = types.ModuleType("protoverta.core")
    core.Protocol = types.SimpleNamespace(CHAT="chat", MESSAGES="messages", RESPONSES="responses")
    server = types.ModuleType("protoverta.server")
    server.GatewayConfig, server.gateway_session = Mock(), Mock()
    capabilities = types.ModuleType("protoverta.capabilities")
    capabilities.CapabilityRegistry = Mock()
    with patch.dict(sys.modules, {"protoverta.core": core, "protoverta.server": server, "protoverta.capabilities": capabilities}), patch("codex_adapter.launcher.resolve_token", return_value="fixture-key"):
        _gateway(config, provider)
    capabilities.CapabilityRegistry.load.assert_called_once_with(tmp_path / "checks.json")
    assert server.GatewayConfig.call_args.kwargs["require_verified_capabilities"] is True


def test_failed_preflight_does_not_open_listener_or_launch_cli():
    provider = Provider("demo", "Demo", "https://example.test/v1", execution_mode="gateway")
    report = Mock()
    report.require.side_effect = ValueError("unsupported=streaming")
    registry = Mock()
    registry.assess.return_value = report
    gateway = types.SimpleNamespace(capabilities=registry, upstream_base_url=provider.base_url, upstream_protocol="responses", upstream_reasoning="provider_default", require_verified_capabilities=True)
    listener = Mock()
    with patch("codex_adapter.launcher._gateway", return_value=(gateway, listener)), patch("codex_adapter.launcher.build_invocation") as build:
        with pytest.raises(LaunchError, match="unsupported=streaming"):
            preflight(Config(), provider, "model-a", "medium")
    listener.assert_not_called()
    build.assert_not_called()
    assert registry.assess.call_args.args[2] == "model-a"
