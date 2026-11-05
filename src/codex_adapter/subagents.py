"""Explicit session opt-in for the verified foreground subagent subset."""

from __future__ import annotations

from .agent_models import validate_agent_models
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
    agent_models = validate_agent_models(agent_models)
    if agent_models.get("policy", "inherit") not in {"selected", "custom"}:
        raise LaunchError("Translated subagents require selected or custom agent model policy with explicit model metadata.")
    if not catalog:
        raise LaunchError("Translated subagents require CODEX_CA_MODEL_CATALOG with an exact model entry; no fallback metadata is inferred.")
    models = {model, agent_models.get("default", model)}
    for selected in models:
        _, entry = load_catalog(catalog, selected)
        if entry.get("supports_search_tool") is not False or entry.get("tool_mode") is not None:
            raise LaunchError(
                "Every parent/child catalog entry requires explicit supports_search_tool=false and no code-only tool mode. Deferred tool search is not certified here."
            )
        if agent_models.get("policy") == "custom" and selected == agent_models["default"]:
            efforts = entry.get("supported_reasoning_levels")
            if not isinstance(efforts, list) or not any(isinstance(item, dict) and item.get("effort") == agent_models["effort"] for item in efforts):
                raise LaunchError("Custom child effort is not explicitly supported by its own catalog entry.")
    return ["features.multi_agent=true", "agents.enabled=true", "agents.max_concurrent_threads_per_session=1"]
