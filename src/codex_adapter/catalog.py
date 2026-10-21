from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config, Provider, cache_path
from .credentials import resolve_token
from .errors import DiscoveryError


def _read_cache(path: Path | None = None) -> dict:
    source = path or cache_path()
    try:
        return json.loads(source.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"providers": {}}


def _write_cache(data: dict, path: Path | None = None) -> None:
    target = path or cache_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def cached_models(provider: Provider, path: Path | None = None) -> tuple[list[str], bool]:
    entry = _read_cache(path).get("providers", {}).get(provider.id, {})
    models = entry.get("models", [])
    stale = time.time() - float(entry.get("fetched_at", 0)) > provider.discovery.cache_ttl_seconds
    return [item for item in models if isinstance(item, str)], stale


def fetch_models(config: Config, provider: Provider, *, cache: Path | None = None, timeout: float = 15.0) -> list[str]:
    if not provider.discovery.enabled:
        raise DiscoveryError(f"Provider '{provider.id}' does not expose model discovery.")
    if provider.discovery.response_format != "openai_models_list":
        raise DiscoveryError(f"Unsupported model discovery format '{provider.discovery.response_format}'.")
    headers = {"Accept": "application/json", "User-Agent": "codex-ai/0.1"}
    token = resolve_token(config, provider, required=False)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(provider.base_url + provider.discovery.path, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise DiscoveryError(f"Model discovery failed for '{provider.id}': HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"Model discovery failed for '{provider.id}': {exc.reason if isinstance(exc, URLError) else type(exc).__name__}.") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise DiscoveryError("Model discovery response does not contain a 'data' list.")
    models = sorted({item.get("id") for item in data if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()})
    if not models:
        raise DiscoveryError("Model discovery returned no usable model IDs.")
    saved = _read_cache(cache)
    saved.setdefault("providers", {})[provider.id] = {"fetched_at": time.time(), "models": models}
    _write_cache(saved, cache)
    return models
