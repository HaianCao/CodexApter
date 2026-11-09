"""Loopback relay that retries failed provider requests before Codex sees them.

Codex retries only rate limits, server errors and dropped connections, after a
few seconds. Some providers fail a request for a while with other errors (for
example AgentRouter routing a turn to a backend that does not hold the earlier
response items: "The requested item was created under a different Azure OpenAI
resource"). The relay waits ``delay_seconds`` and sends the same request again,
up to ``max_retries`` times, when:

* the provider answers with an HTTP error status not listed in ``skip_status``;
* the provider cannot be reached; or
* a Responses stream fails (``error`` / ``response.failed``) or ends before any
  output was produced.

Once output has been forwarded to Codex the stream is passed through unchanged,
so nothing is ever repeated or merged. The relay adds no credentials: Codex's
own ``Authorization`` header is forwarded to the configured provider origin
only. Retries are logged to ``retry.log`` beside the catalog cache, because
Codex owns the terminal.
"""

from __future__ import annotations

import http.client
import json
import select
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, BinaryIO, Self
from urllib.parse import urlsplit

from .config import Retry, cache_path

# Headers that describe one connection, not the request, plus Accept-Encoding so
# streams arrive uncompressed and can be inspected.
_HOP_BY_HOP = {
    "accept-encoding",
    "connection",
    "content-length",
    "host",
    "keep-alive",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_PENDING_EVENTS = {"response.created", "response.in_progress", "response.queued"}
_FAILED_EVENTS = {"error", "response.failed"}
UPSTREAM_TIMEOUT_SECONDS = 300
_CHUNK = 65536


class _ClientGone(Exception):
    """Codex closed the connection (for example the user interrupted the turn)."""


def _log_path() -> Path:
    return cache_path().parent / "retry.log"


def _log(message: str) -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {message}\n")
    except OSError:
        pass


def _summary(body: bytes) -> str:
    try:
        payload = json.loads(body)
    except ValueError:
        return body[:200].decode("utf-8", "replace")
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict) and isinstance(error.get("message"), str):
        return error["message"][:200]
    response = payload.get("response") if isinstance(payload, dict) else None
    if isinstance(response, dict) and isinstance(response.get("error"), dict):
        return str(response["error"].get("message", ""))[:200]
    if isinstance(payload, dict) and isinstance(payload.get("message"), str):
        return payload["message"][:200]
    return body[:200].decode("utf-8", "replace")


def _event_type(block: bytes) -> str | None:
    name = None
    data: list[bytes] = []
    for line in block.splitlines():
        if line.startswith(b"event:"):
            name = line[6:].strip().decode("utf-8", "replace")
        elif line.startswith(b"data:"):
            data.append(line[5:].strip())
    if name:
        return name
    try:
        payload = json.loads(b"\n".join(data))
    except ValueError:
        return None
    return payload.get("type") if isinstance(payload, dict) else None


def _read_until_output(response: BinaryIO) -> tuple[bytes, str, str]:
    """Buffer a Responses stream until it produces output or fails.

    Returns the buffered bytes, ``"ok"``, ``"failed"`` or ``"ended"`` (closed
    before any output), and a short reason for the log.
    """
    buffered = bytearray()
    block = bytearray()
    while True:
        line = response.readline()
        if not line:
            return bytes(buffered), "ended", "stream ended before output"
        buffered += line
        if line.strip():
            block += line
            continue
        if not block:
            continue
        kind = _event_type(bytes(block))
        if kind in _FAILED_EVENTS:
            data = b"\n".join(line[5:].strip() for line in bytes(block).splitlines() if line.startswith(b"data:"))
            return bytes(buffered), "failed", f"stream {kind}: {_summary(data)}"
        if kind not in _PENDING_EVENTS:
            return bytes(buffered), "ok", ""
        block.clear()


class RetryRelay(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, upstream: str, policy: Retry):
        parts = urlsplit(upstream)
        self.origin = f"{parts.scheme}://{parts.netloc}"
        self.policy = policy
        self.opener = urllib.request.build_opener()
        super().__init__(("127.0.0.1", 0), _Handler)

    def base_url(self, upstream: str) -> str:
        return f"http://127.0.0.1:{self.server_port}{urlsplit(upstream).path.rstrip('/')}"

    def __enter__(self) -> Self:
        self._thread = threading.Thread(target=self.serve_forever, name="codexapter-retry-relay", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()
        self.server_close()
        self._thread.join(timeout=5)


class _Handler(BaseHTTPRequestHandler):
    server: RetryRelay
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self._relay()

    def do_POST(self) -> None:
        self._relay()

    def do_DELETE(self) -> None:
        self._relay()

    def _body(self) -> bytes:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            body = bytearray()
            while True:
                size = int(self.rfile.readline().split(b";", 1)[0].strip() or b"0", 16)
                if size == 0:
                    while self.rfile.readline() not in (b"\r\n", b"\n", b""):
                        pass
                    return bytes(body)
                body += self.rfile.read(size)
                self.rfile.readline()
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length) if length else b""

    def _wait(self, attempt: int, reason: str) -> None:
        policy = self.server.policy
        _log(f"{self.command} {self.path}: {reason}; retry {attempt}/{policy.max_retries} in {policy.delay_seconds}s")
        deadline = time.monotonic() + policy.delay_seconds
        while (remaining := deadline - time.monotonic()) > 0:
            readable, _, _ = select.select([self.connection], [], [], min(remaining, 1.0))
            if readable:
                try:
                    if not self.connection.recv(1, socket.MSG_PEEK):
                        raise _ClientGone
                except OSError as exc:
                    raise _ClientGone from exc
                time.sleep(min(remaining, 1.0))

    def _relay(self) -> None:
        self.close_connection = True
        policy = self.server.policy
        body = self._body()
        headers = {name: value for name, value in self.headers.items() if name.lower() not in _HOP_BY_HOP}
        url = self.server.origin + self.path
        attempt = 0
        try:
            while True:
                request = urllib.request.Request(url, data=body if self.command != "GET" else None, headers=headers, method=self.command)
                retryable = attempt < policy.max_retries
                try:
                    response = self.server.opener.open(request, timeout=UPSTREAM_TIMEOUT_SECONDS)
                except urllib.error.HTTPError as error:
                    payload = error.read()
                    if retryable and error.code not in policy.skip_status:
                        attempt += 1
                        self._wait(attempt, f"HTTP {error.code} {_summary(payload)}")
                        continue
                    self._send(error.code, error.headers, payload)
                    return
                except (urllib.error.URLError, OSError, http.client.HTTPException) as error:
                    if retryable:
                        attempt += 1
                        self._wait(attempt, f"provider unreachable: {error}")
                        continue
                    message = json.dumps({"error": {"message": f"Provider unreachable after {attempt} retries: {error}", "type": "codexapter_retry"}}).encode()
                    self._send(502, {"Content-Type": "application/json"}, message)
                    return
                with response:
                    if "text/event-stream" not in response.headers.get("Content-Type", ""):
                        self._send(response.status, response.headers, response.read())
                        return
                    try:
                        prefix, verdict, reason = _read_until_output(response)
                    except (OSError, http.client.HTTPException) as error:
                        prefix, verdict, reason = b"", "ended", f"stream broke before output: {error}"
                    if verdict != "ok" and retryable:
                        attempt += 1
                        self._wait(attempt, reason)
                        continue
                    self._stream(response, prefix)
                    if attempt:
                        _log(f"{self.command} {self.path}: succeeded after {attempt} retries")
                    return
        except _ClientGone:
            _log(f"{self.command} {self.path}: Codex cancelled the request; retries stopped")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _send(self, status: int, headers: Any, payload: bytes) -> None:
        self.send_response(status)
        for name, value in headers.items():
            if name.lower() not in _HOP_BY_HOP and name.lower() != "content-encoding":
                self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def _stream(self, response: http.client.HTTPResponse, prefix: bytes) -> None:
        self.send_response(response.status)
        for name, value in response.headers.items():
            if name.lower() not in _HOP_BY_HOP and name.lower() != "content-encoding":
                self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(prefix)
        self.wfile.flush()
        try:
            while chunk := response.read1(_CHUNK):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException):
            pass  # Codex sees the truncated stream and reports it.
