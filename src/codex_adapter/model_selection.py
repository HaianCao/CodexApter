"""Read-only selection evidence for setup; never opens a gateway or probes."""

from pathlib import Path

from .config import config_path


def model_status(config, provider, model):
    unknown = {"status": "unverified", "message": "Unverified route. Model discovery is not capability certification."}
    if not model or not provider.capabilities_file:
        return unknown
    try:
        from protoverta.automatic_reasoning import automatic_reasoning
        from protoverta.capabilities import CapabilityRegistry
        from protoverta.selection_status import selection_status

        upstream = {"responses": "responses", "chat_completions": "chat", "anthropic_messages": "messages"}[provider.protocol]
        path = Path(provider.capabilities_file).expanduser()
        if not path.is_absolute():
            path = (config.path or config_path()).resolve().parent / path
        reasoning, public = (automatic_reasoning("responses", upstream, provider.base_url) if provider.upstream_reasoning == "auto" else (provider.upstream_reasoning, provider.public_reasoning))
        options = {**provider.gateway_runtime, "upstream_reasoning": reasoning, "public_reasoning": public,
                   "namespace_tools": "flatten", "client_tool_search": provider.client_tool_search}
        return selection_status(CapabilityRegistry.load(path), provider.base_url, upstream, model, source_protocol="responses",
                                client_profile="codex" if upstream != "responses" else "strict", overrides=options,
                                freshness_required=bool(provider.gateway_runtime.get("require_current_evidence") or provider.gateway_runtime.get("evidence_max_age_days") is not None))
    except (ImportError, OSError, ValueError, KeyError, TypeError):
        return unknown
