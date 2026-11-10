"""Opt-in Codex metadata supplied by the operator, never inferred from a name."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import LaunchError
from .toml_text import toml_value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate catalog JSON field")
        result[key] = value
    return result


def validate_native_entry(entry):
    """Required ModelInfo structure observed in Codex CLI 0.166.0."""
    required = {
        "slug": str,
        "display_name": str,
        "supported_reasoning_levels": list,
        "shell_type": str,
        "visibility": str,
        "supported_in_api": bool,
        "priority": int,
        "support_verbosity": bool,
        "truncation_policy": dict,
        "experimental_supported_tools": list,
    }
    if not isinstance(entry, dict) or any(type(entry.get(key)) is not kind for key, kind in required.items()):
        raise ValueError("missing or invalid native ModelInfo field")
    if not entry["slug"] or len(entry["slug"]) > 512 or any(c.isspace() or ord(c) < 32 for c in entry["slug"]):
        raise ValueError("invalid native model slug")
    if not entry["display_name"] or entry["visibility"] not in {"list", "hide"}:
        raise ValueError("invalid native model name or visibility")
    efforts = entry["supported_reasoning_levels"]
    if any(
        not isinstance(item, dict)
        or item.get("effort") not in {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
        or not isinstance(item.get("description"), str)
        for item in efforts
    ):
        raise ValueError("invalid native reasoning levels")
    if len({item["effort"] for item in efforts}) != len(efforts):
        raise ValueError("duplicate native reasoning levels")
    default = entry.get("default_reasoning_level")
    if default is not None and default not in {item["effort"] for item in efforts}:
        raise ValueError("native default effort is absent from supported levels")
    truncation = entry["truncation_policy"]
    if truncation.get("mode") not in {"bytes", "tokens"} or type(truncation.get("limit")) is not int or truncation["limit"] < 0:
        raise ValueError("invalid native truncation policy")
    if any(not isinstance(value, str) for value in entry["experimental_supported_tools"]):
        raise ValueError("invalid native tool names")
    for key in ("context_window", "max_context_window"):
        if entry.get(key) is not None and (type(entry[key]) is not int or entry[key] <= 0):
            raise ValueError("invalid native context metadata")
    if entry.get("context_window") and entry.get("max_context_window") and entry["context_window"] > entry["max_context_window"]:
        raise ValueError("inconsistent native context metadata")
    if entry.get("supports_search_tool") is not None and type(entry["supports_search_tool"]) is not bool:
        raise ValueError("invalid native search capability")


def load_catalog(value: str, model: str) -> tuple[Path, dict]:
    try:
        path = Path(value).expanduser().resolve()
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError
        catalog = json.loads(raw, object_pairs_hook=_unique_object)
        json.dumps(catalog, ensure_ascii=False, allow_nan=False).encode("utf-8")
        models = catalog["models"]
        if not isinstance(models, list) or not models or len(models) > 10000:
            raise ValueError
        slugs = [item["slug"] for item in models]
        if any(not isinstance(slug, str) or not slug.strip() for slug in slugs) or len(set(slugs)) != len(slugs):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError):
        raise LaunchError("CODEX_CA_MODEL_CATALOG must name a readable Codex catalog with unique model slugs (maximum 2 MiB).") from None
    if model not in slugs:
        raise LaunchError("The selected model has no exact entry in CODEX_CA_MODEL_CATALOG; refusing an implicit fallback tool profile.")
    return path, models[slugs.index(model)]


def catalog_override(value: str | None, model: str, prefix=None) -> list[str]:
    if not value:
        return []
    path, _ = load_catalog(value, model)
    if prefix is not None:
        from .harness import detect_version, require_schema

        require_schema(detect_version(prefix), "Custom model catalog (0.166.0 ModelInfo schema)", LaunchError)
    try:
        with path.open("rb") as source:
            raw = source.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError
        catalog = json.loads(raw, object_pairs_hook=_unique_object)
        json.dumps(catalog, ensure_ascii=False, allow_nan=False).encode("utf-8")
        models = catalog["models"]
        if not isinstance(models, list) or not models or len(models) > 10000 or model not in [item.get("slug") for item in models if isinstance(item, dict)]:
            raise ValueError
        for entry in models:
            validate_native_entry(entry)
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        raise LaunchError(
            "Custom catalog must contain complete, valid Codex 0.166.0 ModelInfo entries; unknown metadata is not filled from another model."
        ) from None
    return [f"model_catalog_json={toml_value(str(path), ensure_ascii=True)}"]
