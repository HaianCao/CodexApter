"""Loopback-only tests for the retry relay; no credentials or provider calls."""

import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from codex_adapter.config import Config, Provider, Retry, SecretReference, load, save
from codex_adapter.errors import ConfigError
from codex_adapter.launcher import build_invocation
from codex_adapter.retry_relay import RetryRelay

AZURE = b'{"error":{"message":"The requested item was created under a different Azure OpenAI resource.","type":"invalid_request_error","param":"","code":null}}'


def sse(*events):
    return b"".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode() for event in events)


CREATED = {"type": "response.created", "response": {"id": "r", "status": "in_progress", "output": []}}
DELTA = {"type": "response.output_text.delta", "item_id": "m", "output_index": 0, "content_index": 0, "delta": "PONG"}
COMPLETED = {"type": "response.completed", "response": {"id": "r", "status": "completed", "output": []}}
FAILED = {"type": "response.failed", "response": {"id": "r", "status": "failed", "error": {"code": "server_error", "message": "backend busy"}}}
OK_STREAM = sse(CREATED, DELTA, COMPLETED)


class Upstream:
    """Answers each request with the next scripted (status, content type, body)."""

    def __init__(self, *replies):
        self.replies, self.requests = list(replies), []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                upstream.requests.append((self.path, dict(self.headers), body))
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


def call(url, body=b'{"model":"m","input":"hi"}'):
    request = urllib.request.Request(url + "/responses", data=body, headers={"Authorization": "Bearer local-key", "Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class RetryRelayTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.log = Path(directory.name) / "retry.log"
        cache = patch.dict("os.environ", {"CODEX_CA_CACHE": str(Path(directory.name) / "catalog-cache.json")})
        cache.start()
        self.addCleanup(cache.stop)

    def relay(self, upstream, **policy):
        relay = RetryRelay(upstream.url, Retry(**{"delay_seconds": 0, **policy}))
        relay.__enter__()
        self.addCleanup(relay.__exit__, None, None, None)
        self.addCleanup(upstream.close)
        return relay.base_url(upstream.url)

    def test_http_error_is_retried_until_the_provider_answers(self):
        upstream = Upstream((400, "application/json", AZURE), (400, "application/json", AZURE), (200, "text/event-stream", OK_STREAM))
        status, body = call(self.relay(upstream))
        self.assertEqual((status, body), (200, OK_STREAM))
        self.assertEqual(len(upstream.requests), 3)
        path, headers, sent = upstream.requests[-1]
        self.assertEqual(path, "/v1/responses")
        self.assertEqual(headers["Authorization"], "Bearer local-key")
        self.assertEqual(sent, b'{"model":"m","input":"hi"}')
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("different Azure OpenAI resource", log)
        self.assertIn("retry 2/10", log)
        self.assertIn("succeeded after 2 retries", log)

    def test_last_error_reaches_codex_when_retries_run_out(self):
        upstream = Upstream((400, "application/json", AZURE))
        self.assertEqual(call(self.relay(upstream, max_retries=3)), (400, AZURE))
        self.assertEqual(len(upstream.requests), 4)

    def test_skipped_status_is_not_retried(self):
        upstream = Upstream((401, "application/json", b'{"error":{"message":"bad key"}}'))
        self.assertEqual(call(self.relay(upstream))[0], 401)
        self.assertEqual(len(upstream.requests), 1)

    def test_stream_failing_before_output_is_retried(self):
        upstream = Upstream((200, "text/event-stream", sse(CREATED, FAILED)), (200, "text/event-stream", b""), (200, "text/event-stream", OK_STREAM))
        self.assertEqual(call(self.relay(upstream)), (200, OK_STREAM))
        self.assertEqual(len(upstream.requests), 3)
        log = self.log.read_text(encoding="utf-8")
        self.assertIn("stream response.failed: backend busy", log)
        self.assertIn("stream ended before output", log)

    def test_stream_failing_after_output_is_passed_through(self):
        partial = sse(CREATED, DELTA, FAILED)
        upstream = Upstream((200, "text/event-stream", partial), (200, "text/event-stream", OK_STREAM))
        self.assertEqual(call(self.relay(upstream)), (200, partial))
        self.assertEqual(len(upstream.requests), 1)

    def test_failed_stream_is_forwarded_whole_when_retries_run_out(self):
        failed = sse(CREATED, FAILED)
        upstream = Upstream((200, "text/event-stream", failed))
        self.assertEqual(call(self.relay(upstream, max_retries=1)), (200, failed))
        self.assertEqual(len(upstream.requests), 2)

    def test_unreachable_provider_returns_502_after_retries(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        relay = RetryRelay(f"http://127.0.0.1:{port}/v1", Retry(max_retries=2, delay_seconds=0))
        with relay:
            status, body = call(relay.base_url(f"http://127.0.0.1:{port}/v1"))
        self.assertEqual(status, 502)
        self.assertIn("unreachable after 2 retries", json.loads(body)["error"]["message"])

    def test_cancelled_request_stops_waiting(self):
        upstream = Upstream((400, "application/json", AZURE))
        url = self.relay(upstream, delay_seconds=30)
        port = int(url.split(":")[2].split("/")[0])
        body = b'{"model":"m"}'
        with socket.create_connection(("127.0.0.1", port), timeout=5) as client:
            client.sendall(b"POST /v1/responses HTTP/1.1\r\nHost: x\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            deadline = time.monotonic() + 5
            while not upstream.requests and time.monotonic() < deadline:
                time.sleep(0.05)
        deadline = time.monotonic() + 5
        while "cancelled" not in (self.log.read_text(encoding="utf-8") if self.log.exists() else "") and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertIn("Codex cancelled the request", self.log.read_text(encoding="utf-8"))
        self.assertEqual(len(upstream.requests), 1)


class RetryConfigTests(unittest.TestCase):
    def test_defaults_round_trip_and_custom_values(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            save(Config(path=path))
            self.assertEqual(load(path).retry, Retry())
            path.write_text("schema_version = 1\n\n[retry]\nenabled = true\nmax_retries = 3\ndelay_seconds = 5\nskip_status = [401]\n", encoding="utf-8")
            loaded = load(path)
            self.assertEqual(loaded.retry, Retry(True, 3, 5, (401,)))
            save(loaded)
            self.assertEqual(load(path).retry, Retry(True, 3, 5, (401,)))

    def test_invalid_values_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            for table in ("max_retries = -1", "max_retries = 101", "delay_seconds = 3601", "enabled = 1", 'skip_status = "401"', "skip_status = [200]"):
                path.write_text(f"schema_version = 1\n\n[retry]\n{table}\n", encoding="utf-8")
                with self.subTest(table=table), self.assertRaises(ConfigError):
                    load(path)

    def test_codex_points_at_the_relay_without_its_own_retries(self):
        provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")
        config = Config(providers={"demo": provider}, secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_KEY")})
        with patch("codex_adapter.launcher.shutil.which", return_value="codex.exe"), patch.dict("os.environ", {"DEMO_KEY": "secret"}):
            argv, _ = build_invocation(config, provider, "m", "medium", [], "http://127.0.0.1:5000/v1")
            plain, _ = build_invocation(config, provider, "m", "medium", [])
        self.assertIn('model_providers.demo.base_url="http://127.0.0.1:5000/v1"', argv)
        self.assertIn("model_providers.demo.request_max_retries=0", argv)
        self.assertIn('model_providers.demo.base_url="https://example.test/v1"', plain)
        self.assertNotIn("model_providers.demo.request_max_retries=0", plain)


if __name__ == "__main__":
    unittest.main()
