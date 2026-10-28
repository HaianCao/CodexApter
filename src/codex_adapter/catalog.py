from __future__ import annotations

import hashlib
import json
import math
import os
import time
import uuid
from contextlib import nullcontext
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Config, Provider, migrate_legacy_cache
from .credentials import resolve_token
from .errors import DiscoveryError
from .http_deadline import Deadline, DeadlineHTTPHandler, DeadlineHTTPSHandler
from .settings_store import SettingsGuard

_UNSET = object()
_MESSAGES_API_VERSION = "2023-06-01"


def _decode_json(raw):
    try:
        value = json.loads(raw)
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError):
        raise ValueError("Invalid catalog JSON.") from None
    return value


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _open(request, *, timeout):
    return build_opener(_NoRedirect(), DeadlineHTTPHandler(), DeadlineHTTPSHandler()).open(request, timeout=timeout)


def _identity(provider: Provider, config=None, *, token=_UNSET) -> dict:
    from .gateway_options import catalog_policy_fingerprint

    result = {"base_url": provider.base_url, "protocol": provider.protocol,
            "discovery_path": provider.discovery.path, "response_format": provider.discovery.response_format,
            "auth_secret_ref": provider.auth_secret_ref, "auth_mode": provider.auth_mode,
            "catalog_policy_file": provider.gateway_runtime.get("catalog_policy_file", "")}
    result["catalog_policy_fingerprint"] = catalog_policy_fingerprint(config.path if config else None, provider)
    if provider.protocol == "anthropic_messages":
        result["anthropic_version"] = _MESSAGES_API_VERSION
    if config is not None:
        if token is _UNSET:
            token = resolve_token(config, provider, required=False)
        if token:
            result["credential_fingerprint"] = hashlib.sha256(token.encode()).hexdigest()
    return result


def _read_cache(path: Path | None = None) -> dict:
    source = path or migrate_legacy_cache()
    try:
        data = _decode_json(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
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


def cached_models(provider: Provider, path: Path | None = None, *, config=None) -> tuple[list[str], bool]:
    entry = _read_cache(path).get("providers", {}).get(provider.id, {})
    if not isinstance(entry, dict) or entry.get("identity") != _identity(provider, config):
        return [], True
    models = entry.get("models", [])
    if not isinstance(models, list):
        models = []
    fetched_at = entry.get("fetched_at", 0)
    if isinstance(fetched_at, bool) or not isinstance(fetched_at, (int, float)) or not math.isfinite(fetched_at):
        fetched_at = 0
    stale = time.time() - fetched_at > provider.discovery.cache_ttl_seconds
    return [item for item in models if isinstance(item, str)], stale


def store_catalog(provider_id, entry, cache=None):
    target = cache or migrate_legacy_cache()
    try:
        with SettingsGuard(target).edit():
            saved = _read_cache(target)
            saved.setdefault("providers", {})[provider_id] = entry
            _write_cache(saved, target)
    except (OSError, ValueError):
        pass  # A cache failure must not discard successful discovery.


def fetch_models(config: Config, provider: Provider, *, cache: Path | None = None, timeout: float = 15.0, cache_sink=None, budget=None, token=_UNSET) -> list[str]:
    if not provider.discovery.enabled:
        raise DiscoveryError(f"Provider '{provider.id}' does not expose model discovery.")
    if provider.discovery.response_format != "openai_models_list":
        raise DiscoveryError(f"Unsupported model discovery format '{provider.discovery.response_format}'.")
    headers = {"Accept": "application/json", "User-Agent": "CodexApter/0.1 (codex-ca)"}
    if provider.protocol == "anthropic_messages":
        # Match the Messages contract already used by the gateway and ClaudApter.
        headers["anthropic-version"] = _MESSAGES_API_VERSION
    if token is _UNSET:
        token = resolve_token(config, provider, required=False)
    identity = _identity(provider, config, token=token)
    if token:
        headers["x-api-key" if provider.auth_mode == "api_key" else "Authorization"] = token if provider.auth_mode == "api_key" else f"Bearer {token}"
    if provider.execution_mode == "gateway":
        try:
            from protoverta.discovery import CatalogPolicy, fetch_catalog

            policy_file = provider.gateway_runtime.get("catalog_policy_file")
            policy = CatalogPolicy(path=provider.discovery.path, timeout=timeout, pagination="after_id" if provider.protocol == "anthropic_messages" else "none")
            if policy_file:
                path = Path(policy_file).expanduser()
                if not path.is_absolute():
                    path = (config.path.parent if config.path else Path.cwd()) / path
                policy = CatalogPolicy.load(path, provider.base_url)
            options = {}
            if budget is not None:
                def upstream_open(request, *, timeout):
                    request.adapter_deadline = budget
                    return _open(request, timeout=min(timeout, budget.remaining()))
                options["upstream_open"] = upstream_open
            info = [item.as_dict() for item in fetch_catalog(provider.base_url, headers, policy, **options)]
        except ImportError:
            raise DiscoveryError("Gateway discovery requires the matching Protoverta preview.") from None
        except (ValueError, OSError, TypeError):
            raise DiscoveryError("Gateway catalog failed validation or exceeded its resource budget.") from None
        models = [item["id"] for item in info]
        if not models:
            raise DiscoveryError("Model discovery returned no usable model IDs.")
        entry = {"identity": identity, "fetched_at": time.time(), "models": models, "model_info": info}
        if cache_sink is not None:
            cache_sink(entry)
        else:
            store_catalog(provider.id, entry, cache)
        return models
    request = Request(provider.base_url + provider.discovery.path, headers=headers, method="GET")
    try:
        with (nullcontext(budget) if budget is not None else Deadline(timeout)) as active_budget:
            request.adapter_deadline = active_budget
            with _open(request, timeout=active_budget.remaining()) as response:
                raw = response.read(8_000_001)
                active_budget.remaining()
                if len(raw) > 8_000_000:
                    raise DiscoveryError("Model discovery response is too large (limit: 8 MB).")
                payload = _decode_json(raw)
    except HTTPError as exc:
        exc.close()
        raise DiscoveryError(f"Model discovery failed for '{provider.id}': HTTP {exc.code}.") from None
    except (URLError, OSError, UnicodeError, ValueError, RecursionError, HTTPException) as exc:
        raise DiscoveryError(f"Model discovery failed for '{provider.id}': {type(exc).__name__}.") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise DiscoveryError("Model discovery response does not contain a 'data' list.")
    models = sorted({item["id"].strip() for item in data if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()})
    if not models:
        raise DiscoveryError("Model discovery returned no usable model IDs.")
    entry = {"identity": identity, "fetched_at": time.time(), "models": models}
    if cache_sink is not None:
        cache_sink(entry)
    else:
        store_catalog(provider.id, entry, cache)
    return models
