from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
from pathlib import Path

from .agent_models import agent_overrides
from .command import command_prefix
from .config import (
    Config,
    Provider,
    config_path,
    validate_auth_mode,
    validate_base_url,
    validate_capability_settings,
    validate_client_tool_search,
    validate_public_reasoning,
)
from .credentials import resolve_token
from .errors import LaunchError
from .gateway_options import runtime_options, session_options, validate_gateway_limits
from .harness import cross_protocol_overrides
from .mcp import codex_overrides, load_servers
from .model_catalog import catalog_override


def _bypass_loopback_proxy(env: dict[str, str]) -> None:
    entries = [value.strip() for key in ("NO_PROXY", "no_proxy") for value in env.get(key, "").split(",") if value.strip()]
    entries.extend(("127.0.0.1", "localhost", "::1"))
    merged = ",".join({value.casefold(): value for value in entries}.values())
    env.update(NO_PROXY=merged, no_proxy=merged)


def build_invocation(config: Config, provider: Provider, model: str, reasoning: str, extra: list[str], *, gateway_url: str | None = None, gateway_token: str | None = None) -> tuple[list[str], dict[str, str]]:
    configured_base_url = validate_base_url(provider.base_url)
    validate_auth_mode(provider.auth_mode, provider.execution_mode)
    validate_client_tool_search(provider.client_tool_search, provider.protocol, provider.execution_mode)
    validate_public_reasoning(provider.public_reasoning, provider.protocol, provider.execution_mode)
    validate_capability_settings(provider.capabilities_file, provider.require_verified_capabilities, provider.execution_mode)
    if provider.upstream_reasoning not in ("auto", "provider_default", "disabled", "openrouter_disabled") or (provider.execution_mode != "gateway" and provider.upstream_reasoning not in ("auto", "provider_default")):
        raise LaunchError("An explicit upstream reasoning policy requires gateway mode.")
    executable = shutil.which("codex")
    if not executable:
        raise LaunchError("Codex CLI was not found on PATH.")
    try:
        prefix = command_prefix(executable, "codex", "@openai/codex")
    except ValueError as exc:
        raise LaunchError(str(exc)) from None
    token = resolve_token(config, provider)
    env = os.environ.copy()
    if provider.auth_secret_ref:
        env_name = config.secrets[provider.auth_secret_ref].env_var
        if token and env_name and provider.execution_mode != "gateway":
            env[env_name] = token
        elif provider.execution_mode == "gateway" and env_name:
            env.pop(env_name, None)
    if provider.execution_mode == "gateway":
        if not gateway_url or not gateway_token:
            raise LaunchError("Gateway mode requires a running local Protoverta gateway.")
        env_name = "CODEX_CA_GATEWAY_TOKEN"
        env[env_name] = gateway_token
        base_url = gateway_url.rstrip("/") + "/v1"
        _bypass_loopback_proxy(env)
    elif provider.protocol == "responses" and provider.execution_mode in {"direct", "auto"}:
        base_url = configured_base_url
    else:
        raise LaunchError(f"Provider '{provider.id}' needs gateway mode for protocol '{provider.protocol}'.")
    if not provider.auth_secret_ref:
        raise LaunchError(f"Provider '{provider.id}' needs an API-key secret reference.")
    upstream_env_name = config.secrets[provider.auth_secret_ref].env_var
    if not upstream_env_name:
        raise LaunchError(f"Provider '{provider.id}' has no environment variable for its API key.")
    if provider.execution_mode != "gateway":
        env_name = upstream_env_name
    # These runtime overrides deliberately select the custom provider rather than
    # Codex's default ChatGPT/OpenAI provider. Config keys follow the official
    # Codex custom-provider schema; argv is passed as a list, never via a shell.
    overrides = [
        f'model_provider={json.dumps(provider.id)}',
        f'model_providers.{provider.id}.name={json.dumps(provider.display_name)}',
        f'model_providers.{provider.id}.base_url={json.dumps(base_url)}',
        f'model_providers.{provider.id}.env_key={json.dumps(env_name)}',
        f'model_providers.{provider.id}.wire_api="responses"',
        f'model_providers.{provider.id}.requires_openai_auth=false',
        f'model_providers.{provider.id}.supports_websockets=false',
        f'model_reasoning_effort={json.dumps(reasoning)}',
    ]
    overrides.extend(catalog_override(os.environ.get("CODEX_CA_MODEL_CATALOG"), model))
    try:
        overrides.extend(codex_overrides(load_servers(os.environ.get("CODEX_CA_MCP_CONFIG"))))
    except ValueError as exc:
        raise LaunchError(str(exc)) from None
    if provider.execution_mode == "gateway" and provider.protocol != "responses":
        overrides.extend(cross_protocol_overrides(prefix))
    overrides.extend(agent_overrides(prefix, provider.agent_models, model, reasoning))
    argv = [*prefix, *[piece for setting in overrides for piece in ("-c", setting)], "-m", model, *extra]
    if any(not isinstance(arg, str) or "\0" in arg for arg in argv):
        raise LaunchError("CLI arguments must be strings without NUL characters.")
    return argv, env


def _gateway(config: Config, provider: Provider):
    try:
        from protoverta.core import Protocol
        from protoverta.server import GatewayConfig, gateway_session
    except ImportError as exc:
        raise LaunchError("Gateway mode needs a compatible Protoverta build installed in this Python environment.") from exc
    upstream = {"responses": Protocol.RESPONSES, "chat_completions": Protocol.CHAT, "anthropic_messages": Protocol.MESSAGES}.get(provider.protocol)
    if upstream is None:
        raise LaunchError(f"Unsupported upstream protocol: {provider.protocol}")
    token = resolve_token(config, provider)
    if not token:
        raise LaunchError(f"Provider '{provider.id}' needs an API key for gateway mode.")
    try:
        registry_options = {}
        registry_options.update(runtime_options(provider.gateway_runtime, provider.execution_mode, config.path or config_path()))
        if validate_client_tool_search(provider.client_tool_search, provider.protocol, provider.execution_mode) == "function":
            registry_options["client_tool_search"] = "function"
        registry_options.update(validate_gateway_limits(provider.gateway_limits, provider.execution_mode))
        if validate_public_reasoning(provider.public_reasoning, provider.protocol, provider.execution_mode):
            registry_options["public_reasoning"] = True
        reasoning_policy = provider.upstream_reasoning
        if reasoning_policy == "auto":
            from protoverta.automatic_reasoning import automatic_reasoning

            reasoning_policy, public_text = automatic_reasoning("responses", upstream, provider.base_url)
            registry_options["public_reasoning"] = public_text
        capabilities_file, required = validate_capability_settings(provider.capabilities_file, provider.require_verified_capabilities, provider.execution_mode)
        if capabilities_file:
            from protoverta.capabilities import CapabilityRegistry

            path = Path(capabilities_file).expanduser()
            if not path.is_absolute():
                path = (config.path or config_path()).resolve().parent / path
            registry_options.update(capabilities=CapabilityRegistry.load(path), require_verified_capabilities=required)
        gateway = GatewayConfig(
            upstream, provider.base_url, token, secrets.token_urlsafe(32),
            "x-api-key" if validate_auth_mode(provider.auth_mode, provider.execution_mode) == "api_key" else "bearer",
            client_profile="codex" if upstream != Protocol.RESPONSES else "strict",
            default_max_output_tokens=provider.gateway_max_output_tokens,
            upstream_reasoning=reasoning_policy,
            namespace_tools="flatten" if upstream != Protocol.RESPONSES else "reject",
            messages_system_models=tuple(provider.messages_system_models),
            **registry_options,
        )
    except (ImportError, OSError, TypeError, ValueError) as exc:
        raise LaunchError(f"Invalid or incompatible Protoverta gateway configuration: {exc}") from exc
    return gateway, gateway_session


def _verify_capabilities(gateway, model: str, reasoning: str) -> None:
    registry = getattr(gateway, "capabilities", None)
    if registry is None:
        return
    features = {"text", "streaming"}
    # public_reasoning permits preserving optional output; it does not request
    # that the model emit reasoning. Actual reasoning history is checked by
    # the gateway's per-request capability preflight. The route-policy
    # fingerprint below still includes this decoder setting.
    if gateway.upstream_reasoning == "provider_default":
        features.add("reasoning_effort")
    try:
        report = registry.assess(gateway.upstream_base_url, gateway.upstream_protocol, model, features)
        report.require(verified=gateway.require_verified_capabilities)
        from protoverta.capabilities import route_policies
        from protoverta.evidence_identity import evidence_options

        registry.assess_route(gateway.upstream_base_url, gateway.upstream_protocol, model, features,
                              source_protocol="responses", streaming=True, client_profile=gateway.client_profile,
                              policies=route_policies(gateway), **evidence_options(gateway)).require(verified=gateway.require_verified_routes or gateway.require_current_evidence or gateway.evidence_max_age_days is not None)
    except ValueError as exc:
        raise LaunchError(f"Model compatibility check failed: {exc}") from exc


def preflight(config: Config, provider: Provider, model: str, reasoning: str) -> None:
    """Validate launch prerequisites without opening a listener or CLI process."""
    if provider.execution_mode == "gateway":
        gateway, _ = _gateway(config, provider)
        _verify_capabilities(gateway, model, reasoning)
        build_invocation(config, provider, model, reasoning, [], gateway_url="http://127.0.0.1:0", gateway_token="preflight")
    else:
        build_invocation(config, provider, model, reasoning, [])


def launch(config: Config, provider: Provider, model: str, reasoning: str, extra: list[str]) -> int:
    try:
        if provider.execution_mode == "gateway":
            gateway, gateway_session = _gateway(config, provider)
            _verify_capabilities(gateway, model, reasoning)
            with session_options(config.path or config_path(), provider) as observer_options, gateway_session(gateway, **observer_options) as origin:
                argv, env = build_invocation(config, provider, model, reasoning, extra, gateway_url=origin, gateway_token=gateway.inbound_token)
                return subprocess.run(argv, cwd=Path.cwd(), env=env, check=False).returncode
        argv, env = build_invocation(config, provider, model, reasoning, extra)
        return subprocess.run(argv, cwd=Path.cwd(), env=env, check=False).returncode
    except OSError as exc:
        raise LaunchError(f"Could not start Codex: {exc}") from exc
