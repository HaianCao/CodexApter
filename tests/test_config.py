import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_adapter.config import Config, Defaults, Discovery, ModelAlias, Provider, SecretReference, load, save, validate_base_url
from codex_adapter.credentials import env_file_path, resolve_token, store_token
from codex_adapter.errors import ConfigError


class ConfigTests(unittest.TestCase):
    def test_save_load_and_resolve_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            config = Config(
                defaults=Defaults(provider="demo", model="fast", reasoning_effort="high"),
                providers={"demo": Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key", default_model="fallback", discovery=Discovery(enabled=True))},
                secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_KEY")},
                models={"fast": ModelAlias("fast", "demo", "vendor/fast")},
                path=path,
                env_file=Path(directory) / ".env",
            )
            save(config)
            loaded = load(path)
            provider, model, reasoning = loaded.resolve(None, None, None)
            self.assertEqual(provider.id, "demo")
            self.assertEqual(provider.display_name, "Demo")
            self.assertEqual(model, "vendor/fast")
            self.assertEqual(reasoning, "high")
            self.assertEqual(loaded.env_file, Path(directory) / ".env")

    def test_alias_provider_conflict_is_rejected(self):
        config = Config(
            providers={"one": Provider("one", "One", "https://one.test"), "two": Provider("two", "Two", "https://two.test")},
            models={"fast": ModelAlias("fast", "one", "one-fast")},
        )
        with self.assertRaises(ConfigError):
            config.resolve("two", "fast", None)

    def test_env_file_path_is_shared_for_store_and_resolve(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            config = Config(
                providers={"demo": Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")},
                secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_API_TOKEN")},
                env_file=env_path,
            )
            with patch.dict("os.environ", {}, clear=False):
                import os
                os.environ.pop("DEMO_API_TOKEN", None)
                store_token("DEMO_API_TOKEN", "sample-token", config=config)
                os.environ.pop("DEMO_API_TOKEN", None)
                self.assertEqual(resolve_token(config, config.providers["demo"]), "sample-token")
                self.assertEqual(env_path.read_text(encoding="utf-8"), "DEMO_API_TOKEN=sample-token\n")

    def test_env_file_environment_override_wins_over_saved_path(self):
        configured = Path("configured.env")
        override = Path("override.env")
        config = Config(env_file=configured)
        with patch.dict("os.environ", {"CODEX_AI_ENV_FILE": str(override)}):
            self.assertEqual(env_file_path(config), override)

    def test_installed_app_env_path_is_stable_and_not_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(path=Path(directory) / "config.toml")
            with patch.dict("os.environ", {}, clear=False), patch.object(Path, "is_file", return_value=False):
                import os
                os.environ.pop("CODEX_AI_ENV_FILE", None)
                self.assertEqual(env_file_path(config), Path(directory) / ".env")

    def test_base_url_allows_https_and_exact_loopback_only(self):
        self.assertEqual(validate_base_url("https://api.example.test/v1/"), "https://api.example.test/v1")
        self.assertEqual(validate_base_url("http://localhost:8080/v1"), "http://localhost:8080/v1")
        for value in ("http://localhost.attacker.example/v1", "http://127.0.0.1.attacker.example/v1", "http://user@localhost/v1", "https://@api.example.test/v1", "https://api.example.test/v1?token=x"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_base_url(value)

    def test_setup_can_load_legacy_protocol_for_repair(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.toml"
            path.write_text('''schema_version = 1
[providers.nim]
display_name = "NVIDIA NIM"
base_url = "https://example.test/v1"
protocol = "chat_completions"
execution_mode = "gateway"
''', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(path)
            config = load(path, allow_legacy_protocols=True)
            self.assertEqual(config.providers["nim"].display_name, "NVIDIA NIM")
            self.assertEqual(config.providers["nim"].protocol, "chat_completions")

if __name__ == "__main__":
    unittest.main()
