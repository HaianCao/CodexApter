import tempfile
import unittest
from pathlib import Path

from codex_adapter.config import Config, Defaults, ModelAlias, Provider, SecretReference
from codex_adapter.web import _remove_provider_config


class WebConfigTests(unittest.TestCase):
    def test_delete_provider_removes_unused_secret_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text('DEMO_KEY="secret"\n', encoding="utf-8")
            config = Config(
                defaults=Defaults(provider="demo", model="fast"),
                providers={"demo": Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-ref")},
                secrets={"demo-ref": SecretReference("demo-ref", env_var="DEMO_KEY")},
                models={"fast": ModelAlias("fast", "demo", "model-id")},
                env_file=env_path,
            )
            env_var = _remove_provider_config(config, "demo")
            self.assertEqual(env_var, "DEMO_KEY")
            self.assertFalse(config.providers)
            self.assertFalse(config.secrets)
            self.assertFalse(config.models)
            self.assertIsNone(config.defaults.provider)

    def test_delete_preserves_secret_shared_by_another_provider(self):
        shared = SecretReference("shared", env_var="SHARED_KEY")
        config = Config(
            providers={
                "one": Provider("one", "One", "https://one.test/v1", auth_secret_ref="shared"),
                "two": Provider("two", "Two", "https://two.test/v1", auth_secret_ref="shared"),
            },
            secrets={"shared": shared},
        )
        self.assertIsNone(_remove_provider_config(config, "one"))
        self.assertIn("shared", config.secrets)
        self.assertIn("two", config.providers)


if __name__ == "__main__":
    unittest.main()
