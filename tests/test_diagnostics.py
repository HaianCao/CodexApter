import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

from codex_adapter.config import Config, Provider, SecretReference
from codex_adapter.diagnostics import probe_responses


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"id": "resp_test", "output": []}).encode()


class DiagnosticsTests(unittest.TestCase):
    def test_probe_posts_small_responses_request_and_does_not_log_key(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text("DEMO_TOKEN=secret-value\n", encoding="utf-8")
            provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")
            config = Config(
                providers={"demo": provider},
                secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_TOKEN")},
                env_file=env_path,
            )
            with patch("codex_adapter.diagnostics.urlopen", return_value=_Response()) as open_url:
                result = probe_responses(config, provider, "sample-model")
            request = open_url.call_args.args[0]
            self.assertEqual(request.full_url, "https://example.test/v1/responses")
            self.assertEqual(request.get_method(), "POST")
            self.assertEqual(request.get_header("Authorization"), "Bearer secret-value")
            self.assertIn('"stream": false', request.data.decode())
            self.assertTrue(result.startswith("OK:"))
            self.assertNotIn("secret-value", result)

    def test_probe_skips_non_responses_protocol(self):
        provider = Provider("legacy", "Legacy", "https://example.test/v1", protocol="chat_completions")
        result = probe_responses(Config(providers={"legacy": provider}), provider, "model")
        self.assertIn("SKIP", result)

    def test_probe_reports_http_status_without_echoing_response_body(self):
        provider = Provider("demo", "Demo", "https://example.test/v1")
        error = HTTPError("https://example.test/v1/responses", 404, "Not Found", {}, None)
        with patch("codex_adapter.diagnostics.urlopen", side_effect=error):
            result = probe_responses(Config(providers={"demo": provider}), provider, "model")
        self.assertEqual(result, "FAIL: HTTP 404 from /responses.")


if __name__ == "__main__":
    unittest.main()
