from __future__ import annotations

import os
import json
import secrets
import shutil
import subprocess
from pathlib import Path

from .config import Config, Provider
from .credentials import resolve_token
from .errors import LaunchError


def build_invocation(config: Config, provider: Provider, model: str, reasoning: str, extra: list[str], *, gateway_url: str | None = None, gateway_token: str | None = None) -> tuple[list[str], dict[str, str]]:
    if provider.upstream_reasoning not in ("provider_default", "disabled", "openrouter_disabled") or (provider.execution_mode != "gateway" and provider.upstream_reasoning != "provider_default"):
        raise LaunchError("An explicit upstream reasoning policy requires gateway mode.")
    executable = shutil.which("codex")
    if not executable:
        raise LaunchError("Codex CLI was not found on PATH.")
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
    elif provider.protocol == "responses" and provider.execution_mode in {"direct", "auto"}:
        base_url = provider.base_url
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
    if provider.execution_mode == "gateway" and provider.protocol != "responses":
        overrides.extend(['model_reasoning_summary="none"', 'features.multi_agent=false', 'web_search="disabled"'])
    argv = [executable, *[piece for setting in overrides for piece in ("-c", setting)], "-m", model, *extra]
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
        gateway = GatewayConfig(
            upstream, provider.base_url, token, secrets.token_urlsafe(32),
            "x-api-key" if upstream == Protocol.MESSAGES else "bearer",
            client_profile="codex" if upstream != Protocol.RESPONSES else "strict",
            default_max_output_tokens=provider.gateway_max_output_tokens,
            upstream_reasoning=provider.upstream_reasoning,
        )
    except (TypeError, ValueError) as exc:
        raise LaunchError(f"Invalid or incompatible Protoverta gateway configuration: {exc}") from exc
    return gateway, gateway_session


def preflight(config: Config, provider: Provider, model: str, reasoning: str) -> None:
    """Validate launch prerequisites without opening a listener or CLI process."""
    if provider.execution_mode == "gateway":
        _gateway(config, provider)
        build_invocation(config, provider, model, reasoning, [], gateway_url="http://127.0.0.1:0", gateway_token="preflight")
    else:
        build_invocation(config, provider, model, reasoning, [])


def launch(config: Config, provider: Provider, model: str, reasoning: str, extra: list[str]) -> int:
    try:
        if provider.execution_mode == "gateway":
            gateway, gateway_session = _gateway(config, provider)
            with gateway_session(gateway) as origin:
                argv, env = build_invocation(config, provider, model, reasoning, extra, gateway_url=origin, gateway_token=gateway.inbound_token)
                return subprocess.run(argv, cwd=Path.cwd(), env=env, check=False).returncode
        argv, env = build_invocation(config, provider, model, reasoning, extra)
        return subprocess.run(argv, cwd=Path.cwd(), env=env, check=False).returncode
    except OSError as exc:
        raise LaunchError(f"Could not start Codex: {exc}") from exc
