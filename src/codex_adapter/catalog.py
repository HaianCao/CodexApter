from __future__ import annotations

import json
import math
import os
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import Config, Provider, cache_path, migrate_legacy_cache
from .credentials import resolve_token
from .errors import DiscoveryError


def _read_cache(path: Path | None = None) -> dict:
    source = path or migrate_legacy_cache()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {"providers": {}}
    if not isinstance(data, dict) or not isinstance(data.get("providers"), dict):
        return {"providers": {}}
    return data


def _write_cache(data: dict, path: Path | None = None) -> None:
    target = path or migrate_legacy_cache()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def cached_models(provider: Provider, path: Path | None = None) -> tuple[list[str], bool]:
    entry = _read_cache(path).get("providers", {}).get(provider.id, {})
    if not isinstance(entry, dict):
        return [], True
    models = entry.get("models", [])
    if not isinstance(models, list):
        models = []
    fetched_at = entry.get("fetched_at", 0)
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)) or not math.isfinite(fetched_at):
        fetched_at = 0
    stale = time.time() - fetched_at > provider.discovery.cache_ttl_seconds
    return [item for item in models if isinstance(item, str)], stale


def fetch_models(config: Config, provider: Provider, *, cache: Path | None = None, timeout: float = 15.0) -> list[str]:
    if not provider.discovery.enabled:
        raise DiscoveryError(f"Provider '{provider.id}' does not expose model discovery.")
    if provider.discovery.response_format != "openai_models_list":
        raise DiscoveryError(f"Unsupported model discovery format '{provider.discovery.response_format}'.")
    headers = {"Accept": "application/json", "User-Agent": "CodexApter/0.1 (codex-ca)"}
    token = resolve_token(config, provider, required=False)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(provider.base_url + provider.discovery.path, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(8_000_001)
            if len(raw) > 8_000_000:
                raise DiscoveryError("Model discovery response is too large (limit: 8 MB).")
            payload = json.loads(raw.decode("utf-8"))
    except HTTPError as exc:
        raise DiscoveryError(f"Model discovery failed for '{provider.id}': HTTP {exc.code}.") from exc
    except (URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DiscoveryError(f"Model discovery failed for '{provider.id}': {exc.reason if isinstance(exc, URLError) else type(exc).__name__}.") from exc
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise DiscoveryError("Model discovery response does not contain a 'data' list.")
    models = sorted({item["id"].strip() for item in data if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()})
    if not models:
        raise DiscoveryError("Model discovery returned no usable model IDs.")
    saved = _read_cache(cache)
    saved.setdefault("providers", {})[provider.id] = {"fetched_at": time.time(), "models": models}
    _write_cache(saved, cache)
    return models
