import os
import unittest
from unittest.mock import patch

from codex_adapter.config import Config, Provider, SecretReference
from codex_adapter.launcher import build_invocation


class LauncherTests(unittest.TestCase):
    def test_invocation_uses_argv_and_passes_secret_only_in_environment(self):
        provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")
        config = Config(
            providers={"demo": provider},
            secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_API_KEY")},
        )
        with patch("codex_adapter.launcher.shutil.which", return_value="C:/Codex/codex.exe"), patch.dict(os.environ, {"DEMO_API_KEY": "sensitive-key"}):
            argv, env = build_invocation(config, provider, "sample-model", "high", ["exec", "prompt with spaces"])
        self.assertEqual(argv[0], "C:/Codex/codex.exe")
        self.assertEqual(argv[-3:], ["sample-model", "exec", "prompt with spaces"])
        self.assertEqual(env["DEMO_API_KEY"], "sensitive-key")
        self.assertNotIn("sensitive-key", argv)

    def test_missing_codex_cli_is_reported_cleanly(self):
        provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")
        config = Config(
            providers={"demo": provider},
            secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_API_KEY")},
        )
        with patch("codex_adapter.launcher.shutil.which", return_value=None), patch.dict(os.environ, {"DEMO_API_KEY": "key"}):
            with self.assertRaisesRegex(Exception, "not found on PATH"):
                build_invocation(config, provider, "model", "medium", [])


if __name__ == "__main__":
    unittest.main()
