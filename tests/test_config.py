import os
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_adapter.config import Config, Defaults, Discovery, ModelAlias, Provider, SecretReference, cache_path, config_path, load, save, validate_base_url
from codex_adapter.credentials import env_file_path, remove_token, resolve_token, store_token
from codex_adapter.errors import ConfigError


class ConfigTests(unittest.TestCase):
    def test_messages_system_models_round_trip_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = Provider("demo", "Demo", "https://example.test/v1", protocol="anthropic_messages", execution_mode="gateway", messages_system_models=["verified-model"])
            config = Config(providers={"demo": provider}, path=Path(directory) / "config.toml")
            save(config)
            self.assertEqual(load(config.path).providers["demo"].messages_system_models, ["verified-model"])
            for invalid in ("verified-model", [""], ["bad model"], ["m", "m"], [1]):
                provider.messages_system_models = invalid
                with self.assertRaises(ConfigError):
                    save(config)
            provider.messages_system_models = ["verified-model"]
            provider.protocol = "responses"
            with self.assertRaises(ConfigError):
                save(config)

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
                self.assertEqual(env_path.read_text(encoding="utf-8"), 'DEMO_API_TOKEN="sample-token"\n')

    def test_dotenv_round_trips_quoted_api_keys_and_removes_only_selected_key(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            config = Config(env_file=env_path)
            token = '  key="quoted"  '
            store_token("DEMO_TOKEN", token, config=config)
            store_token("OTHER_TOKEN", "keep-me", config=config)
            os.environ.pop("DEMO_TOKEN", None)
            self.assertEqual(resolve_token(
                Config(
                    providers={"demo": Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")},
                    secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_TOKEN")},
                    env_file=env_path,
                ),
                Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key"),
            ), token)
            remove_token("DEMO_TOKEN", config=config)
            self.assertNotIn("DEMO_TOKEN", env_path.read_text(encoding="utf-8"))
            self.assertIn('OTHER_TOKEN="keep-me"', env_path.read_text(encoding="utf-8"))
            self.assertNotIn("DEMO_TOKEN", os.environ)

    def test_env_file_environment_override_wins_over_saved_path(self):
        configured = Path("configured.env")
        override = Path("override.env")
        config = Config(env_file=configured)
        with patch.dict("os.environ", {"CODEX_CA_ENV_FILE": str(override)}):
            self.assertEqual(env_file_path(config), override)

    def test_installed_app_env_path_is_stable_and_not_current_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Config(path=Path(directory) / "config.toml")
            with patch.dict("os.environ", {}, clear=False), patch.object(Path, "is_file", return_value=False):
                import os
                os.environ.pop("CODEX_CA_ENV_FILE", None)
                self.assertEqual(env_file_path(config), Path(directory) / ".env")

    def test_default_paths_use_codexapter_and_ca_overrides(self):
        with patch.dict("os.environ", {"APPDATA": "C:/Users/test/AppData/Roaming", "LOCALAPPDATA": "C:/Users/test/AppData/Local"}, clear=True):
            self.assertEqual(config_path(), Path("C:/Users/test/AppData/Roaming/codexapter/config.toml"))
            self.assertEqual(cache_path(), Path("C:/Users/test/AppData/Local/codexapter/catalog-cache.json"))
        with patch.dict("os.environ", {"CODEX_CA_CONFIG": "custom.toml", "CODEX_CA_CACHE": "custom-cache.json"}, clear=True):
            self.assertEqual(config_path(), Path("custom.toml"))
            self.assertEqual(cache_path(), Path("custom-cache.json"))

    def test_first_load_migrates_legacy_config_and_dotenv(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = Path(directory) / "roaming"
            old_root, new_root = appdata / "codex-ai", appdata / "codexapter"
            old_root.mkdir(parents=True)
            (old_root / "config.toml").write_text('schema_version = 1\nenv_file = ' + json.dumps(str(old_root / ".env")) + '\n', encoding="utf-8")
            (old_root / ".env").write_text('DEMO_TOKEN="keep"\n', encoding="utf-8")
            with patch.dict("os.environ", {"APPDATA": str(appdata), "LOCALAPPDATA": str(Path(directory) / "local")}, clear=True):
                loaded = load()
            self.assertTrue((new_root / "config.toml").is_file())
            self.assertEqual(loaded.env_file, new_root / ".env")
            self.assertEqual((new_root / ".env").read_text(encoding="utf-8"), 'DEMO_TOKEN="keep"\n')
            self.assertFalse(old_root.exists())

    def test_existing_new_config_wins_without_touching_legacy_data(self):
        with tempfile.TemporaryDirectory() as directory:
            appdata = Path(directory) / "roaming"
            old_root, new_root = appdata / "codex-ai", appdata / "codexapter"
            old_root.mkdir(parents=True)
            new_root.mkdir(parents=True)
            (old_root / "config.toml").write_text('schema_version = 1\n', encoding="utf-8")
            (new_root / "config.toml").write_text('[defaults]\nreasoning_effort = "high"\n', encoding="utf-8")
            with patch.dict("os.environ", {"APPDATA": str(appdata), "LOCALAPPDATA": str(Path(directory) / "local")}, clear=True):
                self.assertEqual(load().defaults.reasoning_effort, "high")
            self.assertTrue((old_root / "config.toml").exists())

    def test_base_url_allows_https_and_exact_loopback_only(self):
        self.assertEqual(validate_base_url("https://api.example.test/v1/"), "https://api.example.test/v1")
        self.assertEqual(validate_base_url("http://localhost:8080/v1"), "http://localhost:8080/v1")
        for value in ("http://localhost.attacker.example/v1", "http://127.0.0.1.attacker.example/v1", "http://user@localhost/v1", "https://@api.example.test/v1", "https://api.example.test/v1?token=x"):
            with self.subTest(value=value), self.assertRaises(ConfigError):
                validate_base_url(value)

    def test_gateway_protocol_is_available_for_normal_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.toml"
            path.write_text('''schema_version = 1
[providers.nim]
display_name = "NVIDIA NIM"
base_url = "https://example.test/v1"
protocol = "chat_completions"
execution_mode = "gateway"
''', encoding="utf-8")
            config = load(path)
            self.assertEqual(config.providers["nim"].display_name, "NVIDIA NIM")
            self.assertEqual(config.providers["nim"].protocol, "chat_completions")
            self.assertEqual(config.providers["nim"].execution_mode, "gateway")

    def test_non_responses_provider_requires_gateway_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.toml"
            path.write_text('''schema_version = 1
[providers.demo]
display_name = "Demo"
base_url = "https://example.test/v1"
protocol = "chat_completions"
execution_mode = "direct"
''', encoding="utf-8")
            with self.assertRaisesRegex(ConfigError, "requires gateway mode"):
                load(path)
            self.assertEqual(load(path, allow_legacy_protocols=True).providers["demo"].protocol, "chat_completions")

    def test_invalid_toml_table_shapes_raise_config_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.toml"
            path.write_text('defaults = "not-a-table"\n', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(path)

    def test_invalid_discovery_types_and_ttl_raise_config_error(self):
        examples = (
            'enabled = "false"\n',
            'cache_ttl_seconds = 0\n',
            'cache_ttl_seconds = true\n',
        )
        for setting in examples:
            with self.subTest(setting=setting), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "invalid.toml"
                path.write_text(
                    """schema_version = 1
[providers.demo]
base_url = "https://example.test/v1"
[providers.demo.model_discovery]
""" + setting,
                    encoding="utf-8",
                )
                with self.assertRaises(ConfigError):
                    load(path)

    def test_invalid_default_reasoning_effort_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.toml"
            path.write_text('[defaults]\nreasoning_effort = "extreme"\n', encoding="utf-8")
            with self.assertRaises(ConfigError):
                load(path)

    def test_schema_version_must_be_integer_one(self):
        for value in ('true', '1.0', '"1"', '2'):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "invalid.toml"
                path.write_text(f"schema_version = {value}\n", encoding="utf-8")
                with self.assertRaises(ConfigError):
                    load(path)

    def test_save_round_trips_control_characters_in_display_name(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            label = 'Demo\t"quoted"\\provider'
            config = Config(providers={"demo": Provider("demo", label, "https://example.test/v1")})
            save(config, path)
            self.assertEqual(load(path).providers["demo"].display_name, label)

if __name__ == "__main__":
    unittest.main()
