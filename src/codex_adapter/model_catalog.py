"""Opt-in Codex metadata supplied by the operator, never inferred from a name."""

from __future__ import annotations

import json
from pathlib import Path

from .errors import LaunchError


def load_catalog(value: str, model: str) -> tuple[Path, dict]:
    try:
        path = Path(value).expanduser().resolve()
        with path.open("rb") as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise ValueError
        catalog = json.loads(raw)
        models = catalog["models"]
        if not isinstance(models, list) or not models:
            raise ValueError
        slugs = [item["slug"] for item in models]
        if any(not isinstance(slug, str) or not slug.strip() for slug in slugs) or len(set(slugs)) != len(slugs):
            raise ValueError
    except (OSError, ValueError, TypeError, KeyError, RecursionError, RuntimeError):
        raise LaunchError("CODEX_CA_MODEL_CATALOG must name a readable Codex catalog with unique model slugs (maximum 2 MiB).") from None
    if model not in slugs:
        raise LaunchError("The selected model has no exact entry in CODEX_CA_MODEL_CATALOG; refusing an implicit fallback tool profile.")
    return path, models[slugs.index(model)]


def catalog_override(value: str | None, model: str) -> list[str]:
    if not value:
        return []
    path, _ = load_catalog(value, model)
    return [f"model_catalog_json={json.dumps(str(path))}"]
