"""Loopback-only tests for the direct-mode retry relay and [retry] config."""

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from protoverta.error_classes import RetryPolicy

from codex_adapter.config import Config, Provider, SecretReference, load, retry_policy, save
from codex_adapter.errors import ConfigError
from codex_adapter.launcher import build_invocation
from codex_adapter.retry_log import RetryLog
from codex_adapter.retry_relay import RetryRelay

AZURE = b'{"error":{"message":"The requested item was created under a different Azure OpenAI resource.","type":"invalid_request_error","param":"","code":null}}'
NO_CHANNEL = '{"error":{"message":"当前分组 default 下对于模型 x 无可用渠道","type":"new_api_error"}}'.encode()
BAD_KEY = b'{"error":{"message":"unauthorized client detected","type":"new_api_error"}}'


def sse(*events):
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


CREATED = {"type": "response.created", "response": {"id": "r", "status": "in_progress", "output": []}}
DELTA = {"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "PONG"}
COMPLETED = {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": []}}
FAILED = {"type": "response.failed", "response": {"id": "r", "status": "failed", "error": {"code": "server_error", "message": "backend busy"}}}
OK_STREAM = sse(CREATED, DELTA, COMPLETED)


class Upstream:
    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                upstream.requests.append(self.path)
                status, kind, payload = upstream.replies.pop(0) if len(upstream.replies) > 1 else upstream.replies[0]
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def call(url):
    request = urllib.request.Request(url + "/responses", data=b'{"model":"m","input":"hi"}', headers={"Authorization": "Bearer key"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


class RelayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log_path = Path(directory.name) / "retry.log"

    def relay(self, upstream, **policy):
        relay = RetryRelay(upstream.url, RetryPolicy(**{"delay_seconds": 0, "jitter": 0, **policy}), "responses", RetryLog(self.log_path))
        relay.__enter__()
        self.addCleanup(relay.__exit__, None, None, None)
        self.addCleanup(upstream.close)
        return relay.base_url

    def log(self):
        return self.log_path.read_text(encoding="utf-8") if self.log_path.exists() else ""

    def test_azure_backend_mismatch_and_no_channel_are_retried(self):
        upstream = Upstream((400, "application/json", AZURE), (503, "application/json", NO_CHANNEL), (200, "text/event-stream", OK_STREAM))
        self.assertEqual(call(self.relay(upstream))[:2], (200, OK_STREAM))
        self.assertEqual(upstream.requests, ["/v1/responses"] * 3)
        self.assertIn("backend_mismatch (HTTP 400)", self.log())
        self.assertIn("overloaded (HTTP 503)", self.log())

    def test_bad_key_is_not_retried(self):
        upstream = Upstream((401, "application/json", BAD_KEY), (200, "text/event-stream", OK_STREAM))
        status, _, headers = call(self.relay(upstream))
        self.assertEqual((status, len(upstream.requests)), (401, 1))
        self.assertEqual(headers.get("x-should-retry"), "false")

    def test_failed_stream_before_output_is_retried_then_forwarded_when_exhausted(self):
        upstream = Upstream((200, "text/event-stream", sse(CREATED, FAILED)), (200, "text/event-stream", OK_STREAM))
        self.assertEqual(call(self.relay(upstream))[:2], (200, OK_STREAM))
        failed = sse(CREATED, FAILED)
        upstream = Upstream((200, "text/event-stream", failed))
        status, body, headers = call(self.relay(upstream, max_retries=1))
        self.assertEqual((status, body), (200, failed))  # Codex reports response.failed itself.
        self.assertEqual(headers.get("x-should-retry"), "false")
        self.assertEqual(len(upstream.requests), 2)

    def test_codex_reconnects_after_giving_up_pass_straight_through(self):
        upstream = Upstream((503, "application/json", NO_CHANNEL))
        url = self.relay(upstream, max_retries=1)
        self.assertEqual(call(url)[0], 503)
        self.assertEqual(call(url)[0], 503)
        self.assertEqual(len(upstream.requests), 3)


class RetryConfigTests(unittest.TestCase):
    def test_0_1_x_table_round_trips_and_defaults_are_not_written(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save(Config(path=path))
            self.assertNotIn("[retry]", path.read_text(encoding="utf-8"))
            self.assertEqual(retry_policy(load(path)), RetryPolicy())
            path.write_text("schema_version = 1\n\n[retry]\nenabled = true\nmax_retries = 3\ndelay_seconds = 5\nskip_status = [401, 403]\n", encoding="utf-8")
            save(load(path))
            self.assertEqual(load(path).retry, {"enabled": True, "max_retries": 3, "delay_seconds": 5, "skip_status": [401, 403]})

    def test_invalid_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            for table in ("max_retries = -1", "delay_seconds = 3601", 'no_retry_classes = ["nope"]', "surprise = 1"):
                path.write_text(f"schema_version = 1\n\n[retry]\n{table}\n", encoding="utf-8")
                with self.subTest(table=table), self.assertRaises(ConfigError):
                    load(path)

    def test_codex_points_at_the_relay_with_one_retry_layer(self):
        provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")
        config = Config(providers={"demo": provider}, secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_KEY")})
        with patch("codex_adapter.launcher.shutil.which", return_value="codex.exe"), patch.dict("os.environ", {"DEMO_KEY": "secret"}):
            relayed, _ = build_invocation(config, provider, "m", "medium", [], relay_url="http://127.0.0.1:5000/v1")
            plain, _ = build_invocation(config, provider, "m", "medium", [])
        self.assertIn('model_providers.demo.base_url="http://127.0.0.1:5000/v1"', relayed)
        self.assertIn("model_providers.demo.request_max_retries=0", relayed)
        self.assertIn("model_providers.demo.stream_idle_timeout_ms=1020000", relayed)  # 10 x 60 s x 1.2 + 300 s.
        self.assertNotIn("model_providers.demo.request_max_retries=0", plain)


if __name__ == "__main__":
    unittest.main()
