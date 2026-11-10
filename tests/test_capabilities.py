import sys
import types
from unittest.mock import Mock, patch

import pytest

from codexapter.config import Config, Provider, load, save
from codexapter.errors import ConfigError, LaunchError
from codexapter.launcher import _gateway, _verify_capabilities, preflight
from codexapter.web import public_config


def test_registry_settings_persist_and_require_gateway(tmp_path):
    provider = Provider(
        "demo", "Demo", "https://example.test/v1", execution_mode="gateway", capabilities_file="checks.json", require_verified_capabilities=True
    )
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
    provider = Provider(
        "demo", "Demo", "https://example.test/v1", execution_mode="gateway", capabilities_file="checks.json", require_verified_capabilities=True
    )
    config = Config(providers={"demo": provider}, path=tmp_path / "config.toml")
    core = types.ModuleType("protoverta.core")
    core.Protocol = types.SimpleNamespace(CHAT="chat", MESSAGES="messages", RESPONSES="responses")
    server = types.ModuleType("protoverta.server")
    server.GatewayConfig, server.gateway_session = Mock(), Mock()
    capabilities = types.ModuleType("protoverta.capabilities")
    capabilities.CapabilityRegistry = Mock()
    with (
        patch.dict(sys.modules, {"protoverta.core": core, "protoverta.server": server, "protoverta.capabilities": capabilities}),
        patch("codexapter.launcher.resolve_token", return_value="fixture-key"),
    ):
        _gateway(config, provider)
    capabilities.CapabilityRegistry.load.assert_called_once_with(tmp_path / "checks.json")
    assert server.GatewayConfig.call_args.kwargs["require_verified_capabilities"] is True


def test_failed_preflight_does_not_open_listener_or_launch_cli():
    provider = Provider("demo", "Demo", "https://example.test/v1", execution_mode="gateway")
    report = Mock()
    report.require.side_effect = ValueError("unsupported=streaming")
    registry = Mock()
    registry.assess.return_value = report
    gateway = types.SimpleNamespace(
        capabilities=registry,
        upstream_base_url=provider.base_url,
        upstream_protocol="responses",
        upstream_reasoning="provider_default",
        require_verified_capabilities=True,
    )
    listener = Mock()
    with (
        patch("codexapter.launcher._gateway", return_value=(gateway, listener)),
        patch("codexapter.launcher.build_invocation") as build,
        pytest.raises(LaunchError, match="unsupported=streaming"),
    ):
        preflight(Config(), provider, "model-a", "medium")
    listener.assert_not_called()
    build.assert_not_called()
    assert registry.assess.call_args.args[2] == "model-a"


@pytest.mark.parametrize("verified", [False, True])
def test_optional_reasoning_permission_does_not_require_model_to_emit_it(verified):
    capabilities = pytest.importorskip("protoverta.capabilities")
    from protoverta.server import GatewayConfig

    registry = capabilities.CapabilityRegistry(
        (
            capabilities.CapabilityRecord(
                "https://example.test/v1",
                "chat",
                "model-a",
                {"text": True, "streaming": True, "reasoning_effort": True, "public_reasoning": False},
                "2026-10-27",
                "synthetic non-public-reasoning model",
            ),
        )
    )
    gateway = GatewayConfig(
        "chat", "https://example.test/v1", "synthetic", "local", public_reasoning=True, capabilities=registry, require_verified_capabilities=verified
    )
    _verify_capabilities(gateway, "model-a", "medium")
    # Permission is not a capability demand. Actual reasoning replay still is.
    with pytest.raises(ValueError, match="unsupported=public_reasoning"):
        registry.preflight(
            gateway.upstream_base_url,
            "chat",
            {
                "model": "model-a",
                "messages": [{"role": "assistant", "content": "answer", "reasoning_content": "actual reasoning"}],
            },
            require_verified=verified,
        )


def test_optional_reasoning_still_requires_matching_route_policy_evidence():
    from dataclasses import replace

    capabilities = pytest.importorskip("protoverta.capabilities")
    from protoverta.server import GatewayConfig

    features = {"text": True, "streaming": True, "reasoning_effort": True, "public_reasoning": False}
    record = capabilities.CapabilityRecord("https://example.test/v1", "chat", "model-a", features, "2026-10-27", "synthetic")
    registry = capabilities.CapabilityRegistry((record,))
    gateway = GatewayConfig(
        "chat",
        "https://example.test/v1",
        "synthetic",
        "local",
        public_reasoning=True,
        capabilities=registry,
        require_verified_capabilities=True,
        require_verified_routes=True,
    )
    with pytest.raises(LaunchError, match="unverified"):
        _verify_capabilities(gateway, "model-a", "medium")
    route = capabilities.RouteCapabilityRecord(
        gateway.upstream_base_url,
        "chat",
        "model-a",
        "responses",
        True,
        gateway.client_profile,
        capabilities.route_policies(gateway),
        features,
        "2026-10-27",
        "synthetic route",
        "synthetic fixture",
    )
    gateway = replace(gateway, capabilities=capabilities.CapabilityRegistry((record,), (route,)))
    _verify_capabilities(gateway, "model-a", "medium")
    with pytest.raises(LaunchError, match="unverified"):
        _verify_capabilities(replace(gateway, public_reasoning=False), "model-a", "medium")
