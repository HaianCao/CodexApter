import sys
import types
from unittest.mock import Mock, patch

import pytest

from codexapter.config import Config, Provider, load, save, validate_public_reasoning
from codexapter.errors import ConfigError
from codexapter.launcher import _gateway
from codexapter.web import public_config


def test_unsigned_public_reasoning_is_explicit_persistent_and_forwarded(tmp_path):
    provider = Provider(
        "nim", "NIM", "https://example.test/v1", protocol="chat", execution_mode="gateway", public_reasoning=True
    )
    config = Config(providers={"nim": provider}, path=tmp_path / "config.toml")
    save(config)
    restored = load(config.path)
    assert restored.providers["nim"].public_reasoning is True
    assert public_config(restored)["providers"][0]["public_reasoning"] is True
    core = types.ModuleType("protoverta.core")
    core.Protocol = types.SimpleNamespace(CHAT="chat", MESSAGES="messages", RESPONSES="responses")
    server = types.ModuleType("protoverta.server")
    server.GatewayConfig, server.gateway_session = Mock(), Mock()
    with (
        patch.dict(sys.modules, {"protoverta.core": core, "protoverta.server": server}),
        patch("codexapter.launcher.resolve_token", return_value="synthetic-key"),
    ):
        _gateway(config, provider)
    assert server.GatewayConfig.call_args.kwargs["public_reasoning"] is True


@pytest.mark.parametrize(
    "value,protocol,mode",
    [
        ("false", "chat", "gateway"),
        (True, "responses", "gateway"),
        (True, "messages", "gateway"),
        (True, "chat", "direct"),
    ],
)
def test_unsigned_reasoning_cannot_be_enabled_for_wrong_protocol_or_mode(value, protocol, mode):
    with pytest.raises(ConfigError):
        validate_public_reasoning(value, protocol, mode)
