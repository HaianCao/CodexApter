import unittest
import contextlib
import io
from unittest.mock import patch

from codex_adapter.cli import main
from codex_adapter.config import Config, Provider


class CliTests(unittest.TestCase):
    def test_help_uses_new_command_name(self):
        output = io.StringIO()
        with patch("sys.argv", ["codex-ca", "--help"]), contextlib.redirect_stdout(output), self.assertRaises(SystemExit):
            main()
        self.assertIn("usage: codex-ca", output.getvalue())

    def _config(self):
        return Config(providers={"demo": Provider("demo", "Demo", "https://example.test/v1")})

    def test_doctor_returns_nonzero_for_failed_check(self):
        config = self._config()
        with patch("codex_adapter.cli.load", return_value=config), patch(
            "codex_adapter.cli.doctor", return_value=[("Responses", "FAIL: HTTP 404")]
        ):
            self.assertEqual(main(["doctor", "-p", "demo", "-m", "test-model"]), 1)

    def test_doctor_returns_zero_for_warnings_without_failure(self):
        config = self._config()
        with patch("codex_adapter.cli.load", return_value=config), patch(
            "codex_adapter.cli.doctor", return_value=[("Model discovery", "WARN: unavailable")]
        ):
            self.assertEqual(main(["doctor", "-p", "demo", "-m", "test-model"]), 0)


if __name__ == "__main__":
    unittest.main()
