import tempfile
import tomllib
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
import sys

from codex_adapter.config import Config, Provider, SecretReference
from codex_adapter.errors import LaunchError
from codex_adapter.launcher import build_invocation, launch


class GatewayLaunchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.provider = Provider(
            "chat", "Chat upstream", "https://example.test/v1",
            protocol="chat_completions", execution_mode="gateway", auth_secret_ref="chat-main",
        )
        self.config = Config(
            providers={"chat": self.provider},
            secrets={"chat-main": SecretReference("chat-main", env_var="CHAT_TOKEN")},
            path=Path(self.directory.name) / "config.toml",
        )

    def test_gateway_invocation_routes_codex_to_loopback_only(self):
        with patch("codex_adapter.launcher.shutil.which", return_value="codex.exe"), patch.dict("os.environ", {"CHAT_TOKEN": "upstream-secret"}), patch(
            "codex_adapter.launcher.resolve_token", return_value="upstream-secret"
        ):
            argv, env = build_invocation(
                self.config, self.provider, "model-id", "medium", [],
                gateway_url="http://127.0.0.1:8765", gateway_token="local-secret",
            )
        self.assertIn('model_providers.chat.base_url="http://127.0.0.1:8765/v1"', argv)
        self.assertIn('model_providers.chat.env_key="CODEX_CA_GATEWAY_TOKEN"', argv)
        self.assertEqual(env["CODEX_CA_GATEWAY_TOKEN"], "local-secret")
        self.assertNotIn("CHAT_TOKEN", env)
        self.assertNotIn("upstream-secret", " ".join(argv))

    def test_gateway_provider_cannot_accidentally_launch_direct(self):
        with patch("codex_adapter.launcher.shutil.which", return_value="codex.exe"), patch(
            "codex_adapter.launcher.resolve_token", return_value="upstream-secret"
        ), self.assertRaises(LaunchError):
            build_invocation(self.config, self.provider, "model-id", "medium", [])

    def test_display_name_quotes_are_valid_toml_overrides(self):
        self.provider.display_name = 'My "Router"'
        with patch("codex_adapter.launcher.shutil.which", return_value="codex.exe"), patch(
            "codex_adapter.launcher.resolve_token", return_value="upstream-secret"
        ):
            argv, _env = build_invocation(self.config, self.provider, "model-id", "medium", [], gateway_url="http://127.0.0.1:8765", gateway_token="local-secret")
        setting = next(item for item in argv if item.startswith("model_providers.chat.name="))
        self.assertEqual(tomllib.loads(setting)["model_providers"]["chat"]["name"], self.provider.display_name)

    def test_gateway_is_closed_after_codex_process_exits(self):
        events = []

        class Protocol:
            CHAT = "chat"
            MESSAGES = "messages"
            RESPONSES = "responses"

        class GatewayConfig:
            def __init__(self, upstream, base_url, key, inbound_token, auth_style, **policy):
                self.inbound_token = inbound_token
                events.append((upstream, base_url, key, auth_style))
                assert policy == {"client_profile": "codex", "default_max_output_tokens": 4096}

        @contextmanager
        def gateway_session(_config):
            events.append("open")
            try:
                yield "http://127.0.0.1:8765"
            finally:
                events.append("closed")

        package = types.ModuleType("protoverta")
        package.__path__ = []
        core = types.ModuleType("protoverta.core")
        core.Protocol = Protocol
        server = types.ModuleType("protoverta.server")
        server.GatewayConfig = GatewayConfig
        server.gateway_session = gateway_session
        with patch.dict(sys.modules, {"protoverta": package, "protoverta.core": core, "protoverta.server": server}), patch(
            "codex_adapter.launcher.resolve_token", return_value="upstream-secret"
        ), patch("codex_adapter.launcher.shutil.which", return_value="codex.exe"), patch(
            "codex_adapter.launcher.subprocess.run", return_value=types.SimpleNamespace(returncode=7)
        ) as run:
            self.assertEqual(launch(self.config, self.provider, "model-id", "medium", []), 7)
        self.assertEqual(events[0], ("chat", "https://example.test/v1", "upstream-secret", "bearer"))
        self.assertEqual(events[1:], ["open", "closed"])
        self.assertTrue(run.call_args.kwargs["env"]["CODEX_CA_GATEWAY_TOKEN"])
        self.assertIn('model_providers.chat.base_url="http://127.0.0.1:8765/v1"', run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
