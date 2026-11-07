"""Read-only selection evidence for setup; never opens a gateway or probes."""

from pathlib import Path

from .config import config_path


def _fallback_status(provider):
    runtime = provider.gateway_runtime if provider else {}
    preflight = runtime.get("context_preflight", "off") or "off"
    count_mode = runtime.get("token_count_mode", "off") or "off"
    protocol = getattr(provider, "protocol", "responses")
    if count_mode == "native":
        count_category = "exact" if protocol in ("responses", "messages") else "unavailable"
    elif count_mode == "estimate" or preflight in ("estimate", "reject_estimate"):
        count_category = "estimate"
    else:
        count_category = "unavailable"
    return {
        "status": "unverified",
        "message": f"Unverified route. Model discovery is not capability certification. Token count: {count_category}.",
        "context_preflight": preflight,
        "token_count_mode": count_mode,
        "count_category": count_category,
        "context_window_tokens": None,
    }


def model_status(config, provider, model):
    unknown = _fallback_status(provider)
    if not model or not provider.capabilities_file:
        return unknown
    try:
        from protoverta.automatic_reasoning import automatic_reasoning
        from protoverta.capabilities import CapabilityRegistry
        from protoverta.selection_status import selection_status

        upstream = provider.protocol
        path = Path(provider.capabilities_file).expanduser()
        if not path.is_absolute():
            path = (config.path or config_path()).resolve().parent / path
        reasoning, public = (
            automatic_reasoning("responses", upstream, provider.base_url)
            if provider.upstream_reasoning == "auto"
            else (provider.upstream_reasoning, provider.public_reasoning)
        )
        options = {
            **provider.gateway_runtime,
            "upstream_reasoning": reasoning,
            "public_reasoning": public,
            "namespace_tools": "flatten",
            "client_tool_search": provider.client_tool_search,
        }
        return selection_status(
            CapabilityRegistry.load(path),
            provider.base_url,
            upstream,
            model,
            source_protocol="responses",
            client_profile="codex" if upstream != "responses" else "strict",
            overrides=options,
            freshness_required=bool(
                provider.gateway_runtime.get("require_current_evidence") or provider.gateway_runtime.get("evidence_max_age_days") is not None
            ),
        )
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return unknown
