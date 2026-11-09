from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .config import Config, Provider
from .credentials import resolve_token
from .errors import LaunchError


def build_invocation(config: Config, provider: Provider, model: str, reasoning: str, extra: list[str], relay_url: str | None = None) -> tuple[list[str], dict[str, str]]:
    executable = shutil.which("codex")
    if not executable:
        raise LaunchError("Codex CLI was not found on PATH.")
    token = resolve_token(config, provider)
    env = os.environ.copy()
    if provider.auth_secret_ref:
        env_name = config.secrets[provider.auth_secret_ref].env_var
        if token and env_name:
            env[env_name] = token
    if provider.protocol != "responses" or provider.execution_mode not in {"direct", "auto"}:
        raise LaunchError(f"Provider '{provider.id}' must use protocol='responses' and direct execution.")
    if not provider.auth_secret_ref:
        raise LaunchError(f"Provider '{provider.id}' needs an API-key secret reference for direct launch.")
    env_name = config.secrets[provider.auth_secret_ref].env_var
    if not env_name:
        raise LaunchError(f"Provider '{provider.id}' has no environment variable for its API key.")
    # These runtime overrides deliberately select the custom provider rather than
    # Codex's default ChatGPT/OpenAI provider. Config keys follow the official
    # Codex custom-provider schema; argv is passed as a list, never via a shell.
    overrides = [
        f'model_provider="{provider.id}"',
        f'model_providers.{provider.id}.name="{provider.display_name}"',
        f'model_providers.{provider.id}.base_url="{relay_url or provider.base_url}"',
        f'model_providers.{provider.id}.env_key="{env_name}"',
        f'model_providers.{provider.id}.wire_api="responses"',
        f'model_providers.{provider.id}.requires_openai_auth=false',
        f'model_providers.{provider.id}.supports_websockets=false',
        f'model_reasoning_effort="{reasoning}"',
    ]
    if relay_url:
        # The relay already waits and retries; Codex's own quick retries on top
        # would repeat every relay cycle.
        overrides.append(f"model_providers.{provider.id}.request_max_retries=0")
    argv = [executable, *[piece for setting in overrides for piece in ("-c", setting)], "-m", model, *extra]
    return argv, env


def _run(argv: list[str], env: dict[str, str]) -> int:
    try:
        return subprocess.run(argv, cwd=Path.cwd(), env=env, check=False).returncode
    except OSError as exc:
        raise LaunchError(f"Could not start Codex: {exc}") from exc


def launch(config: Config, provider: Provider, model: str, reasoning: str, extra: list[str]) -> int:
    policy = config.retry
    if not policy.enabled or policy.max_retries == 0:
        return _run(*build_invocation(config, provider, model, reasoning, extra))
    from .retry_relay import RetryRelay

    with RetryRelay(provider.base_url, policy) as relay:
        return _run(*build_invocation(config, provider, model, reasoning, extra, relay.base_url(provider.base_url)))
