"""Explicit session opt-in for the verified foreground subagent subset."""

from __future__ import annotations

from .errors import LaunchError
from .harness import detect_version
from .model_catalog import load_catalog


def subagent_overrides(prefix, enabled, catalog, model, agent_models):
    if enabled not in (None, "0", "1"):
        raise LaunchError("CODEX_CA_ENABLE_SUBAGENTS accepts 0 or 1 only.")
    if enabled != "1":
        return []
    if detect_version(prefix) != (0, 166, 0):
        raise LaunchError("Translated subagent opt-in is verified only with Codex CLI 0.166.0.")
    if agent_models.get("policy", "inherit") != "selected":
        raise LaunchError("Translated subagents currently require agent_models policy='selected' (same provider/model).")
    if not catalog:
        raise LaunchError("Translated subagents require CODEX_CA_MODEL_CATALOG with an exact model entry; no fallback metadata is inferred.")
    _, entry = load_catalog(catalog, model)
    if entry.get("supports_search_tool") is not False or entry.get("tool_mode") is not None:
        raise LaunchError("The verified subagent subset requires explicit supports_search_tool=false and no code-only tool mode in the model catalog. Deferred tool search is not certified here.")
    return ["features.multi_agent=true", "agents.enabled=true", "agents.max_concurrent_threads_per_session=1"]
