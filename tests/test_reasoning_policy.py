import tempfile
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from codex_adapter.config import Config, Provider, load, save
from codex_adapter.errors import LaunchError
from codex_adapter.launcher import _gateway, build_invocation
from codex_adapter.web import public_config


class ReasoningPolicyTests(unittest.TestCase):
    def test_policy_survives_save_load_and_public_config(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = Provider("demo", "Demo", "https://example.test/v1", execution_mode="gateway", upstream_reasoning="openrouter_disabled")
            config = Config(providers={"demo": provider}, path=Path(directory) / "config.toml")
            save(config)
            restored = load(config.path)
            self.assertEqual(restored.providers["demo"].upstream_reasoning, "openrouter_disabled")
            self.assertEqual(public_config(restored)["providers"][0]["upstream_reasoning"], "openrouter_disabled")

    def test_direct_mode_cannot_silently_ignore_policy(self):
        provider = Provider("demo", "Demo", "https://example.test/v1", upstream_reasoning="disabled")
        with self.assertRaises(LaunchError):
            build_invocation(Config(), provider, "m", "high", [])

    def test_gateway_receives_policy(self):
        provider = Provider("demo", "Demo", "https://example.test/v1", execution_mode="gateway", upstream_reasoning="openrouter_disabled")
        gateway_config = Mock()
        core = types.ModuleType("protoverta.core")
        core.Protocol = types.SimpleNamespace(CHAT="chat", MESSAGES="messages", RESPONSES="responses")
        server = types.ModuleType("protoverta.server")
        server.GatewayConfig, server.gateway_session = gateway_config, Mock()
        with patch.dict(sys.modules, {"protoverta.core": core, "protoverta.server": server}), patch("codex_adapter.launcher.resolve_token", return_value="fake"):
            _gateway(Config(), provider)
        self.assertEqual(gateway_config.call_args.kwargs["upstream_reasoning"], "openrouter_disabled")
