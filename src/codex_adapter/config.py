from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import ConfigError

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PROTOCOLS = {"responses"}
_MODES = {"auto", "direct"}


def config_path() -> Path:
    override = os.environ.get("CODEX_AI_CONFIG")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "codex-ai" / "config.toml"
    return Path.home() / ".config" / "codex-ai" / "config.toml"


def cache_path() -> Path:
    override = os.environ.get("CODEX_AI_CACHE")
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "codex-ai" / "catalog-cache.json"
    return Path.home() / ".cache" / "codex-ai" / "catalog-cache.json"


@dataclass
class Defaults:
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str = "medium"
    execution_mode: str = "auto"


@dataclass
class Discovery:
    enabled: bool = False
    path: str = "/models"
    method: str = "GET"
    response_format: str = "openai_models_list"
    cache_ttl_seconds: int = 3600


@dataclass
class Provider:
    id: str
    display_name: str
    base_url: str
    protocol: str = "responses"
    execution_mode: str = "direct"
    auth_secret_ref: str | None = None
    default_model: str | None = None
    discovery: Discovery = field(default_factory=Discovery)


@dataclass
class SecretReference:
    name: str
    backend: str = "environment"
    env_var: str | None = None


@dataclass
class ModelAlias:
    name: str
    provider: str
    model_id: str
    reasoning_effort: str | None = None


@dataclass
class Config:
    defaults: Defaults = field(default_factory=Defaults)
    providers: dict[str, Provider] = field(default_factory=dict)
    secrets: dict[str, SecretReference] = field(default_factory=dict)
    models: dict[str, ModelAlias] = field(default_factory=dict)
    path: Path | None = None
    env_file: Path | None = None

    def resolve(self, provider: str | None, model: str | None, reasoning: str | None) -> tuple[Provider, str, str]:
        requested_provider = provider or self.defaults.provider
        requested_model = model or self.defaults.model
        if requested_model and requested_model in self.models:
            alias = self.models[requested_model]
            if requested_provider and requested_provider != alias.provider:
                raise ConfigError(f"Model alias '{alias.name}' belongs to provider '{alias.provider}'.")
            requested_provider = alias.provider
            requested_model = alias.model_id
            reasoning = reasoning or alias.reasoning_effort
        if not requested_provider:
            raise ConfigError("No default provider. Run 'codex-ai setup'.")
        selected = self.providers.get(requested_provider)
        if not selected:
            raise ConfigError(f"Unknown provider '{requested_provider}'.")
        resolved_model = requested_model or selected.default_model
        if not resolved_model:
            raise ConfigError("No model selected. Run 'codex-ai setup'.")
        return selected, resolved_model, reasoning or self.defaults.reasoning_effort


def _string(value: Any, field_name: str, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{field_name}' must be a non-empty string.")
    return value.strip()


def _check_id(value: str, label: str) -> None:
    if not _ID.fullmatch(value):
        raise ConfigError(f"{label} '{value}' may contain only lowercase letters, digits, '_' and '-'.")


def validate_base_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        _ = parsed.port  # Force validation of a malformed port number.
    except ValueError as exc:
        raise ConfigError("Provider base URL is invalid.") from exc
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ConfigError("Provider base URL must be an HTTPS URL without credentials, query, or fragment.")
    if parsed.scheme.lower() != "https" and not (parsed.scheme.lower() == "http" and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}):
        raise ConfigError("Provider base URL must use HTTPS (HTTP is allowed only for localhost/loopback).")
    return value.rstrip("/")


def load(path: Path | None = None, *, allow_legacy_protocols: bool = False) -> Config:
    source = path or config_path()
    if not source.exists():
        return Config(path=source)
    try:
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {source}: {exc}") from exc
    if raw.get("schema_version", 1) != 1:
        raise ConfigError("Unsupported schema_version.")
    defaults_raw = raw.get("defaults", {})
    defaults = Defaults(
        provider=_string(defaults_raw.get("provider"), "defaults.provider"),
        model=_string(defaults_raw.get("model"), "defaults.model"),
        reasoning_effort=str(defaults_raw.get("reasoning_effort", "medium")),
        execution_mode=str(defaults_raw.get("execution_mode", "auto")),
    )
    if defaults.execution_mode not in _MODES:
        raise ConfigError("defaults.execution_mode must be auto or direct.")
    secrets: dict[str, SecretReference] = {}
    for name, item in raw.get("secrets", {}).items():
        _check_id(name, "Secret reference")
        if not isinstance(item, dict):
            raise ConfigError(f"secrets.{name} must be a table.")
        backend = str(item.get("backend", "environment"))
        env_var = _string(item.get("env_var"), f"secrets.{name}.env_var")
        if backend != "environment" or not env_var:
            raise ConfigError(f"secrets.{name} currently supports backend='environment' with env_var.")
        secrets[name] = SecretReference(name, backend, env_var)
    providers: dict[str, Provider] = {}
    for name, item in raw.get("providers", {}).items():
        _check_id(name, "Provider")
        if not isinstance(item, dict):
            raise ConfigError(f"providers.{name} must be a table.")
        base_url = _string(item.get("base_url"), f"providers.{name}.base_url", True)
        base_url = validate_base_url(base_url)
        protocol = str(item.get("protocol", "responses"))
        mode = str(item.get("execution_mode", "direct"))
        legacy_protocols = {"chat_completions", "anthropic_messages"} if allow_legacy_protocols else set()
        legacy_modes = {"gateway"} if allow_legacy_protocols else set()
        if protocol not in (_PROTOCOLS | legacy_protocols) or mode not in (_MODES | legacy_modes):
            raise ConfigError(f"providers.{name} has unsupported protocol or execution_mode.")
        secret_ref = _string(item.get("auth_secret_ref"), f"providers.{name}.auth_secret_ref")
        if secret_ref and secret_ref not in secrets:
            raise ConfigError(f"providers.{name} references missing secret '{secret_ref}'.")
        discovery_raw = item.get("model_discovery", {})
        if not isinstance(discovery_raw, dict):
            raise ConfigError(f"providers.{name}.model_discovery must be a table.")
        discovery = Discovery(
            enabled=bool(discovery_raw.get("enabled", False)),
            path=str(discovery_raw.get("path", "/models")),
            method=str(discovery_raw.get("method", "GET")).upper(),
            response_format=str(discovery_raw.get("response_format", "openai_models_list")),
            cache_ttl_seconds=int(discovery_raw.get("cache_ttl_seconds", 3600)),
        )
        if discovery.method != "GET" or not discovery.path.startswith("/") or "://" in discovery.path:
            raise ConfigError(f"providers.{name}.model_discovery only permits a relative GET path.")
        providers[name] = Provider(name, str(item.get("display_name", name)), base_url, protocol, mode, secret_ref, _string(item.get("default_model"), f"providers.{name}.default_model"), discovery)
    models: dict[str, ModelAlias] = {}
    for name, item in raw.get("models", {}).items():
        _check_id(name, "Model alias")
        if not isinstance(item, dict):
            raise ConfigError(f"models.{name} must be a table.")
        provider = _string(item.get("provider"), f"models.{name}.provider", True)
        if provider not in providers:
            raise ConfigError(f"models.{name} references missing provider '{provider}'.")
        models[name] = ModelAlias(name, provider, _string(item.get("model_id"), f"models.{name}.model_id", True), _string(item.get("reasoning_effort"), f"models.{name}.reasoning_effort"))
    configured_env_file = _string(raw.get("env_file"), "env_file")
    config = Config(defaults, providers, secrets, models, source, Path(configured_env_file).expanduser() if configured_env_file else None)
    if defaults.provider and defaults.provider not in providers:
        raise ConfigError(f"defaults.provider references missing provider '{defaults.provider}'.")
    return config


def _quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def save(config: Config, path: Path | None = None) -> Path:
    target = path or config.path or config_path()
    lines = ["schema_version = 1", "", "[defaults]"]
    if config.env_file:
        lines.insert(1, f"env_file = {_quote(str(config.env_file))}")
    for key, value in (("provider", config.defaults.provider), ("model", config.defaults.model), ("reasoning_effort", config.defaults.reasoning_effort), ("execution_mode", config.defaults.execution_mode)):
        if value:
            lines.append(f"{key} = {_quote(value)}")
    for name, secret in sorted(config.secrets.items()):
        lines.extend(["", f"[secrets.{name}]", f"backend = {_quote(secret.backend)}", f"env_var = {_quote(secret.env_var or '')}"])
    for name, provider in sorted(config.providers.items()):
        lines.extend(["", f"[providers.{name}]", f"display_name = {_quote(provider.display_name)}", f"base_url = {_quote(provider.base_url)}", f"protocol = {_quote(provider.protocol)}", f"execution_mode = {_quote(provider.execution_mode)}"])
        if provider.auth_secret_ref:
            lines.append(f"auth_secret_ref = {_quote(provider.auth_secret_ref)}")
        if provider.default_model:
            lines.append(f"default_model = {_quote(provider.default_model)}")
        d = provider.discovery
        lines.extend(["", f"[providers.{name}.model_discovery]", f"enabled = {str(d.enabled).lower()}", f"path = {_quote(d.path)}", f"method = {_quote(d.method)}", f"response_format = {_quote(d.response_format)}", f"cache_ttl_seconds = {d.cache_ttl_seconds}"])
    for name, alias in sorted(config.models.items()):
        lines.extend(["", f"[models.{name}]", f"provider = {_quote(alias.provider)}", f"model_id = {_quote(alias.model_id)}"])
        if alias.reasoning_effort:
            lines.append(f"reasoning_effort = {_quote(alias.reasoning_effort)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target
