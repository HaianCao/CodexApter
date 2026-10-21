from __future__ import annotations

import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .catalog import fetch_models
from .config import Config, Provider
from .credentials import resolve_token


def probe_responses(config: Config, provider: Provider, model: str, *, timeout: float = 20.0) -> str:
    """Send one small, non-streaming text request; this does not test tools or SSE."""
    if provider.protocol != "responses":
        return f"SKIP: configured protocol is '{provider.protocol}', not Responses."
    try:
        token = resolve_token(config, provider)
    except Exception as exc:
        return f"SKIP: credentials unavailable ({exc})."

    headers = {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "codex-ai/0.1"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps({"model": model, "input": "Reply with exactly: pong", "stream": False, "max_output_tokens": 16}).encode("utf-8")
    request = Request(f"{provider.base_url.rstrip('/')}/responses", data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        return f"FAIL: HTTP {exc.code} from /responses."
    except (URLError, TimeoutError) as exc:
        reason = exc.reason if isinstance(exc, URLError) else "request timed out"
        return f"FAIL: {reason}."
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "FAIL: endpoint did not return valid JSON."
    if status < 200 or status >= 300:
        return f"FAIL: HTTP {status} from /responses."
    if not isinstance(payload, dict) or not isinstance(payload.get("output"), list):
        return "FAIL: HTTP success, but response is not a Responses object (missing output list)."
    return "OK: basic non-streaming Responses request succeeded; tools/streaming not tested."


def doctor(config: Config, provider: Provider, model: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = [("Config schema", "OK"), ("Model", model)]
    try:
        resolve_token(config, provider)
        rows.append(("Credentials", "OK"))
    except Exception as exc:
        rows.append(("Credentials", f"FAIL: {exc}"))
    if provider.discovery.enabled:
        try:
            models = fetch_models(config, provider)
            rows.append(("Model discovery", f"OK ({len(models)} models)"))
        except Exception as exc:
            rows.append(("Model discovery", f"WARN: {exc}"))
    else:
        rows.append(("Model discovery", "Unavailable (manual model IDs allowed)"))
    rows.append(("Responses basic request", probe_responses(config, provider, model)))
    rows.append(("Tools/streaming", "Not tested by doctor; requires separate contract tests."))
    return rows
