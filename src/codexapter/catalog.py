from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid
from contextlib import nullcontext
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .config import Config, Provider, cache_path, migrate_legacy_cache
from .credentials import resolve_token
from .errors import DiscoveryError
from .http_deadline import Deadline, DeadlineHTTPHandler, DeadlineHTTPSHandler, attach_deadline
from .settings_store import SettingsGuard

_UNSET = object()
_MESSAGES_API_VERSION = "2023-06-01"


def cached_model_names(config, provider, identifiers, *, token=_UNSET):
    """Enrich explicit session IDs only from a fresh, identity-matched cache."""
    try:
        with cache_path().open("rb") as source:
            raw = source.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return {}
        entry = _decode_json(raw).get("providers", {}).get(provider.id, {})
        if entry.get("identity") != _identity(provider, config, token=token):
            return {}
        saved, now = entry.get("fetched_at"), time.time()
        if (
            type(saved) not in (int, float)
            or not 0 <= saved <= now
            or now - saved > provider.discovery.cache_ttl_seconds
        ):
            return {}
        wanted = set(identifiers)
        info = entry.get("model_info", [])
        if not isinstance(info, list) or len(info) > 10000:
            return {}
        return {
            item["id"]: item["display_name"]
            for item in info
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item["id"] in wanted
            and isinstance(item.get("display_name"), str)
            and 1 <= len(item["display_name"]) <= 512
            and not any(ord(c) < 32 or ord(c) == 127 or 0xD800 <= ord(c) <= 0xDFFF for c in item["display_name"])
        }
    except (OSError, ValueError, TypeError, AttributeError, RecursionError):
        return {}


# Catalog documents are shallow. Python 3.14 parses far deeper JSON without
# RecursionError, so bound nesting explicitly instead of relying on the parser.
MAX_JSON_DEPTH = 64


def _within_depth(value, limit=MAX_JSON_DEPTH):
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if isinstance(item, dict | list):
            if depth > limit:
                return False
            pending.extend((child, depth + 1) for child in (item.values() if isinstance(item, dict) else item))
    return True


def _decode_json(raw):
    try:
        value = json.loads(raw)
        if not _within_depth(value):
            raise ValueError("catalog JSON nesting is too deep")
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

    result = {
        "base_url": provider.base_url,
        "protocol": provider.protocol,
        "discovery_path": provider.discovery.path,
        "response_format": provider.discovery.response_format,
        "auth_secret_ref": provider.auth_secret_ref,
        "auth_mode": provider.auth_mode,
        "catalog_policy_file": provider.gateway_runtime.get("catalog_policy_file", ""),
    }
    result["catalog_policy_fingerprint"] = catalog_policy_fingerprint(config.path if config else None, provider)
    if provider.protocol == "messages":
        result["anthropic_version"] = _MESSAGES_API_VERSION
    if config is not None:
        if token is _UNSET:
            token = resolve_token(config, provider, required=False)
        if token:
            result["credential_fingerprint"] = hashlib.sha256(token.encode()).hexdigest()
    return result


# Token limits that providers report in their model lists. These are the
# provider's own numbers (OpenRouter and TokenHarbor report them; NVIDIA NIM
# does not); nothing is estimated. The first positive integer found wins.
_CONTEXT_FIELDS = (
    "context_window_tokens",
    "context_window",
    "context_length",
    "max_input_tokens",
    "top_provider.context_length",
)
_OUTPUT_FIELDS = ("max_output_tokens", "max_completion_tokens", "top_provider.max_completion_tokens")
_GATEWAY_CONTEXT_FIELD, _GATEWAY_OUTPUT_FIELD = "context_length", "top_provider.max_completion_tokens"
_MAX_REPORTED_TOKENS = 2**31 - 1


def _reported_number(entry, paths):
    for path in paths:
        value = entry
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if type(value) is int and 0 < value <= _MAX_REPORTED_TOKENS:
            return value
    return None


def reported_limits(entry) -> dict:
    """``context_window_tokens`` and ``max_output_tokens`` a model list reports, or ``None``."""
    return {
        "context_window_tokens": _reported_number(entry, _CONTEXT_FIELDS),
        "max_output_tokens": _reported_number(entry, _OUTPUT_FIELDS),
    }


def _present_limits(entry) -> dict:
    """Only the limits that were reported, so models without them keep their usual shape."""
    return {key: value for key, value in reported_limits(entry).items() if value is not None}


def cached_model_limits(config, provider, model, *, token=_UNSET) -> dict:
    """Reported limits for ``model`` from this provider's catalog cache, or ``{}``."""
    try:
        with cache_path().open("rb") as source:
            raw = source.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            return {}
        entry = _decode_json(raw).get("providers", {}).get(provider.id, {})
        if not isinstance(entry, dict) or entry.get("identity") != _identity(provider, config, token=token):
            return {}
        info = entry.get("model_info", [])
        if not isinstance(info, list) or len(info) > 100000:
            return {}
        item = next((value for value in info if isinstance(value, dict) and value.get("id") == model), None)
        return reported_limits(item) if item else {}
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError, RecursionError):
        return {}


def cached_limits(config, provider, *, token=_UNSET) -> dict:
    """``{model_id: reported limits}`` for models whose limits the provider reported."""
    try:
        entry = _read_cache().get("providers", {}).get(provider.id, {})
        if not isinstance(entry, dict) or entry.get("identity") != _identity(provider, config, token=token):
            return {}
        info = entry.get("model_info", [])
        if not isinstance(info, list):
            return {}
        result = {}
        for item in info[:100000]:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                limits = reported_limits(item)
                if any(limits.values()):
                    result[item["id"]] = limits
        return result
    except (OSError, RuntimeError, ValueError, TypeError, AttributeError, RecursionError):
        return {}


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


def _note_skipped(items):
    """Tell the user when the provider's catalog had entries that were left out."""
    skipped = getattr(items, "skipped", 0)
    if skipped:
        noun = "entry" if skipped == 1 else "entries"
        print(f"Note: skipped {skipped} invalid {noun} in the provider's model list.", file=sys.stderr)


def cached_models(provider: Provider, path: Path | None = None, *, config=None) -> tuple[list[str], bool]:
    entry = _read_cache(path).get("providers", {}).get(provider.id, {})
    if not isinstance(entry, dict) or entry.get("identity") != _identity(provider, config):
        return [], True
    models = entry.get("models", [])
    if not isinstance(models, list):
        models = []
    fetched_at, now = entry.get("fetched_at"), time.time()
    # Avoid float conversion of untrusted, potentially huge JSON integers.
    stale = (
        isinstance(fetched_at, bool)
        or not isinstance(fetched_at, int | float)
        or not 0 <= fetched_at <= now
        or now - fetched_at > provider.discovery.cache_ttl_seconds
    )
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


def fetch_models(
    config: Config,
    provider: Provider,
    *,
    cache: Path | None = None,
    timeout: float = 15.0,
    cache_sink=None,
    budget=None,
    token=_UNSET,
) -> list[str]:
    if not provider.discovery.enabled:
        raise DiscoveryError(f"Provider '{provider.id}' does not expose model discovery.")
    if provider.discovery.response_format != "openai_models_list":
        raise DiscoveryError(f"Unsupported model discovery format '{provider.discovery.response_format}'.")
    headers = {"Accept": "application/json", "User-Agent": "CodexApter/0.1 (codex-ca)"}
    if provider.protocol == "messages":
        # Match the Messages contract already used by the gateway and ClaudApter.
        headers["anthropic-version"] = _MESSAGES_API_VERSION
    if token is _UNSET:
        token = resolve_token(config, provider, required=False)
    identity = _identity(provider, config, token=token)
    if token:
        headers["x-api-key" if provider.auth_mode == "api_key" else "Authorization"] = (
            token if provider.auth_mode == "api_key" else f"Bearer {token}"
        )
    if provider.execution_mode == "gateway":
        try:
            from protoverta.discovery import CatalogPolicy, fetch_catalog

            policy_file = provider.gateway_runtime.get("catalog_policy_file")
            policy = CatalogPolicy(
                path=provider.discovery.path,
                timeout=timeout,
                pagination="after_id" if provider.protocol == "messages" else "none",
                context_field=_GATEWAY_CONTEXT_FIELD,
                output_field=_GATEWAY_OUTPUT_FIELD,
                invalid_entries="skip",
            )
            if policy_file:
                path = Path(policy_file).expanduser()
                if not path.is_absolute():
                    path = (config.path.parent if config.path else Path.cwd()) / path
                policy = CatalogPolicy.load(path, provider.base_url)
            options = {}
            if budget is not None:

                def upstream_open(request, *, timeout):
                    attach_deadline(request, budget)
                    return _open(request, timeout=min(timeout, budget.remaining()))

                options["upstream_open"] = upstream_open
            # Skip mode lists every model with a valid ID; malformed optional
            # metadata such as token limits is dropped, never guessed.
            items = fetch_catalog(provider.base_url, headers, policy, **options)
            _note_skipped(items)
            info = [item.as_dict() for item in items]
        except ImportError:
            raise DiscoveryError(
                "Gateway discovery needs the Protoverta component. Run the installer command again to repair it."
            ) from None
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
        with nullcontext(budget) if budget is not None else Deadline(timeout) as active_budget:
            attach_deadline(request, active_budget)
            with _open(request, timeout=active_budget.remaining()) as response:
                raw = response.read(8_000_001)
                active_budget.remaining()
                if len(raw) > 8_000_000:
                    raise DiscoveryError("Model discovery response is too large (limit: 8 MB).")
                payload = _decode_json(raw)
    except HTTPError as exc:
        try:
            body = exc.read(4096) if exc.fp is not None else b""
        except (OSError, ValueError):
            body = b""
        exc.close()
        from .connection_probe import friendly_http_error

        raise DiscoveryError(f"{provider.display_name}: {friendly_http_error(exc.code, body)}") from None
    except (URLError, OSError, UnicodeError, ValueError, RecursionError, HTTPException) as exc:
        raise DiscoveryError(f"Model discovery failed for '{provider.id}': {type(exc).__name__}.") from None
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise DiscoveryError("Model discovery response does not contain a 'data' list.")
    models = sorted(
        {
            item["id"].strip()
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip()
        }
    )
    if not models:
        raise DiscoveryError("Model discovery returned no usable model IDs.")
    info = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip() and len(info) < 10000:
            limits = _present_limits(item)
            if limits:
                info.append({"id": item["id"].strip(), "display_name": item["id"].strip(), **limits})
    entry = {"identity": identity, "fetched_at": time.time(), "models": models}
    if info:
        entry["model_info"] = info
    if cache_sink is not None:
        cache_sink(entry)
    else:
        store_catalog(provider.id, entry, cache)
    return models
