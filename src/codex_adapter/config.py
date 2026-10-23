from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import ConfigError

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_PROTOCOLS = {"responses", "chat_completions", "anthropic_messages"}
_MODES = {"auto", "direct", "gateway"}
_REASONING_EFFORTS = {"low", "medium", "high"}


def config_path() -> Path:
    override = os.environ.get("CODEX_CA_CONFIG")
    if override:
        return Path(override).expanduser()
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "codexapter" / "config.toml"
    return Path.home() / ".config" / "codexapter" / "config.toml"


def cache_path() -> Path:
    override = os.environ.get("CODEX_CA_CACHE")
    if override:
        return Path(override).expanduser()
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "codexapter" / "catalog-cache.json"
    return Path.home() / ".cache" / "codexapter" / "catalog-cache.json"


def _migrate_legacy_config(target: Path) -> Path:
    """Copy legacy user data once; never overwrite an existing CodexApter file."""
    if os.environ.get("CODEX_CA_CONFIG"):
        return target
    appdata = os.environ.get("APPDATA")
    legacy = (Path(appdata) / "codex-ai" / "config.toml") if appdata else Path.home() / ".config" / "codex-ai" / "config.toml"
    if target.exists() or not legacy.is_file():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(legacy, target)
    old_env, new_env = legacy.parent / ".env", target.parent / ".env"
    migrated_env = False
    if old_env.is_file() and not new_env.exists():
        shutil.copy2(old_env, new_env)
        migrated_env = True
    elif old_env.is_file() and new_env.is_file():
        try:
            existing = {line.split("=", 1)[0].strip() for line in new_env.read_text(encoding="utf-8").splitlines() if "=" in line and not line.lstrip().startswith("#")}
            old_lines = old_env.read_text(encoding="utf-8").splitlines()
            additions = [line for line in old_lines if "=" in line and not line.lstrip().startswith("#") and line.split("=", 1)[0].strip() not in existing]
            if additions:
                with new_env.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(("\n" if new_env.stat().st_size else "") + "\n".join(additions) + "\n")
            migrated_env = True
        except OSError:
            pass
    # Rewrite the standard dotenv path if the old config stored it explicitly.
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
        if raw.get("env_file") and Path(raw["env_file"]).expanduser() == old_env:
            content = target.read_text(encoding="utf-8")
            new_line = f"env_file = {json.dumps(str(new_env), ensure_ascii=False)}"
            target.write_text(re.sub(r"(?m)^env_file\s*=.*$", lambda _match: new_line, content, count=1), encoding="utf-8")
    except (OSError, tomllib.TOMLDecodeError, TypeError):
        pass
    if not old_env.exists() or migrated_env:
        legacy.unlink(missing_ok=True)
    if migrated_env:
        old_env.unlink(missing_ok=True)
    try:
        legacy.parent.rmdir()
    except OSError:
        pass
    return target


def migrate_legacy_cache(target: Path | None = None) -> Path:
    target = target or cache_path()
    if os.environ.get("CODEX_CA_CACHE"):
        return target
    local = os.environ.get("LOCALAPPDATA")
    if local:
        legacy = Path(local) / "codex-ai" / "catalog-cache.json"
    else:
        try:
            legacy = Path.home() / ".cache" / "codex-ai" / "catalog-cache.json"
        except RuntimeError:
            return target
    if not target.exists() and legacy.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, target)
        legacy.unlink(missing_ok=True)
    return target


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
    gateway_max_output_tokens: int = 4096
    upstream_reasoning: str = "provider_default"
    messages_system_models: list[str] = field(default_factory=list)
    capabilities_file: str = ""
    require_verified_capabilities: bool = False


def validate_capability_settings(path: Any, required: Any, mode: str) -> tuple[str, bool]:
    if not isinstance(path, str) or "\0" in path or not isinstance(required, bool):
        raise ConfigError("Capability registry path must be text and require_verified_capabilities must be boolean.")
    path = path.strip()
    if (path or required) and mode != "gateway":
        raise ConfigError("Capability checks require gateway mode.")
    if required and not path:
        raise ConfigError("Choose a capability registry before requiring verified capabilities.")
    return path, required


def validate_system_models(value: Any, protocol: str, mode: str) -> list[str]:
    """Explicit, provider-verified model IDs; never infer capability from a name."""
    if not isinstance(value, list) or any(not isinstance(model, str) or not model or any(char.isspace() for char in model) for model in value):
        raise ConfigError("messages_system_models must be an array of exact model IDs.")
    if len(value) != len(set(value)):
        raise ConfigError("messages_system_models cannot contain duplicates.")
    if value and (protocol != "anthropic_messages" or mode != "gateway"):
        raise ConfigError("messages_system_models requires a Messages provider in gateway mode.")
    return list(value)


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
            raise ConfigError("No default provider. Run 'codex-ca setup'.")
        selected = self.providers.get(requested_provider)
        if not selected:
            raise ConfigError(f"Unknown provider '{requested_provider}'.")
        resolved_model = requested_model or selected.default_model
        if not resolved_model:
            raise ConfigError("No model selected. Run 'codex-ca setup'.")
        return selected, resolved_model, reasoning or self.defaults.reasoning_effort


def _string(value: Any, field_name: str, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"'{field_name}' must be a non-empty string.")
    return value.strip()


def _table(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"'{field_name}' must be a TOML table.")
    return value


def _boolean(value: Any, field_name: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"'{field_name}' must be true or false.")
    return value


def _positive_integer(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConfigError(f"'{field_name}' must be a positive integer.")
    return value


def _reasoning_effort(value: Any, field_name: str) -> str:
    effort = _string(value, field_name, required=True)
    if effort not in _REASONING_EFFORTS:
        raise ConfigError(f"'{field_name}' must be low, medium, or high.")
    return effort


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
    source = path or _migrate_legacy_config(config_path())
    if path is None:
        migrate_legacy_cache()
    if not source.exists():
        return Config(path=source)
    try:
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {source}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read configuration {source}: {exc}") from exc
    schema_version = raw.get("schema_version", 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        raise ConfigError("Unsupported schema_version.")
    defaults_raw = _table(raw.get("defaults", {}), "defaults")
    secrets_raw = _table(raw.get("secrets", {}), "secrets")
    providers_raw = _table(raw.get("providers", {}), "providers")
    models_raw = _table(raw.get("models", {}), "models")
    reasoning_effort = _reasoning_effort(defaults_raw.get("reasoning_effort", "medium"), "defaults.reasoning_effort")
    execution_mode = _string(defaults_raw.get("execution_mode", "auto"), "defaults.execution_mode", required=True)
    if execution_mode not in {"auto", "direct"}:
        raise ConfigError("defaults.execution_mode must be auto or direct; gateway mode is configured per provider.")
    defaults = Defaults(
        provider=_string(defaults_raw.get("provider"), "defaults.provider"),
        model=_string(defaults_raw.get("model"), "defaults.model"),
        reasoning_effort=reasoning_effort,
        execution_mode=execution_mode,
    )
    secrets: dict[str, SecretReference] = {}
    for name, item in secrets_raw.items():
        _check_id(name, "Secret reference")
        item = _table(item, f"secrets.{name}")
        backend = _string(item.get("backend", "environment"), f"secrets.{name}.backend", required=True)
        env_var = _string(item.get("env_var"), f"secrets.{name}.env_var")
        if backend != "environment" or not env_var:
            raise ConfigError(f"secrets.{name} currently supports backend='environment' with env_var.")
        secrets[name] = SecretReference(name, backend, env_var)
    providers: dict[str, Provider] = {}
    for name, item in providers_raw.items():
        _check_id(name, "Provider")
        item = _table(item, f"providers.{name}")
        base_url = _string(item.get("base_url"), f"providers.{name}.base_url", True)
        base_url = validate_base_url(base_url)
        protocol = _string(item.get("protocol", "responses"), f"providers.{name}.protocol", required=True)
        mode = _string(item.get("execution_mode", "direct"), f"providers.{name}.execution_mode", required=True)
        if protocol not in _PROTOCOLS or mode not in _MODES:
            raise ConfigError(f"providers.{name} has unsupported protocol or execution_mode.")
        if protocol != "responses" and mode != "gateway" and not allow_legacy_protocols:
            raise ConfigError(f"providers.{name} requires gateway mode for {protocol}.")
        secret_ref = _string(item.get("auth_secret_ref"), f"providers.{name}.auth_secret_ref")
        if secret_ref and secret_ref not in secrets:
            raise ConfigError(f"providers.{name} references missing secret '{secret_ref}'.")
        discovery_raw = item.get("model_discovery", {})
        discovery_raw = _table(discovery_raw, f"providers.{name}.model_discovery")
        discovery = Discovery(
            enabled=_boolean(discovery_raw.get("enabled", False), f"providers.{name}.model_discovery.enabled"),
            path=_string(discovery_raw.get("path", "/models"), f"providers.{name}.model_discovery.path", required=True),
            method=_string(discovery_raw.get("method", "GET"), f"providers.{name}.model_discovery.method", required=True).upper(),
            response_format=_string(discovery_raw.get("response_format", "openai_models_list"), f"providers.{name}.model_discovery.response_format", required=True),
            cache_ttl_seconds=_positive_integer(discovery_raw.get("cache_ttl_seconds", 3600), f"providers.{name}.model_discovery.cache_ttl_seconds"),
        )
        if discovery.method != "GET" or discovery.response_format != "openai_models_list" or not discovery.path.startswith("/") or "://" in discovery.path:
            raise ConfigError(f"providers.{name}.model_discovery only permits a relative GET path.")
        display_name = _string(item.get("display_name", name), f"providers.{name}.display_name", required=True)
        gateway_max_output_tokens = _positive_integer(item.get("gateway_max_output_tokens", 4096), f"providers.{name}.gateway_max_output_tokens")
        upstream_reasoning = item.get("upstream_reasoning", "provider_default")
        if upstream_reasoning not in ("provider_default", "disabled", "openrouter_disabled") or (mode != "gateway" and upstream_reasoning != "provider_default"):
            raise ConfigError(f"providers.{name}.upstream_reasoning requires a valid policy and gateway mode.")
        system_models = validate_system_models(item.get("messages_system_models", []), protocol, mode)
        capabilities_file, require_verified = validate_capability_settings(item.get("capabilities_file", ""), item.get("require_verified_capabilities", False), mode)
        providers[name] = Provider(name, display_name, base_url, protocol, mode, secret_ref, _string(item.get("default_model"), f"providers.{name}.default_model"), discovery, gateway_max_output_tokens, upstream_reasoning, system_models, capabilities_file, require_verified)
    models: dict[str, ModelAlias] = {}
    for name, item in models_raw.items():
        _check_id(name, "Model alias")
        item = _table(item, f"models.{name}")
        provider = _string(item.get("provider"), f"models.{name}.provider", True)
        if provider not in providers:
            raise ConfigError(f"models.{name} references missing provider '{provider}'.")
        alias_reasoning = item.get("reasoning_effort")
        models[name] = ModelAlias(name, provider, _string(item.get("model_id"), f"models.{name}.model_id", True), _reasoning_effort(alias_reasoning, f"models.{name}.reasoning_effort") if alias_reasoning is not None else None)
    configured_env_file = _string(raw.get("env_file"), "env_file")
    config = Config(defaults, providers, secrets, models, source, Path(configured_env_file).expanduser() if configured_env_file else None)
    if defaults.provider and defaults.provider not in providers:
        raise ConfigError(f"defaults.provider references missing provider '{defaults.provider}'.")
    return config


def _quote(value: str) -> str:
    # JSON string escapes are a compatible subset of TOML basic-string escapes.
    return json.dumps(value, ensure_ascii=False)


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
        capabilities_file, require_verified = validate_capability_settings(provider.capabilities_file, provider.require_verified_capabilities, provider.execution_mode)
        if provider.upstream_reasoning not in ("provider_default", "disabled", "openrouter_disabled") or (provider.execution_mode != "gateway" and provider.upstream_reasoning != "provider_default"):
            raise ConfigError(f"providers.{name}.upstream_reasoning requires a valid policy and gateway mode.")
        lines.extend(["", f"[providers.{name}]", f"display_name = {_quote(provider.display_name)}", f"base_url = {_quote(provider.base_url)}", f"protocol = {_quote(provider.protocol)}", f"execution_mode = {_quote(provider.execution_mode)}"])
        if provider.execution_mode == "gateway":
            lines.append(f"gateway_max_output_tokens = {provider.gateway_max_output_tokens}")
        lines.append(f"upstream_reasoning = {_quote(provider.upstream_reasoning)}")
        if capabilities_file:
            lines.extend([f"capabilities_file = {_quote(capabilities_file)}", f"require_verified_capabilities = {str(require_verified).lower()}"])
        system_models = validate_system_models(provider.messages_system_models, provider.protocol, provider.execution_mode)
        if system_models:
            lines.append(f"messages_system_models = {json.dumps(system_models, ensure_ascii=False)}")
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
    content = "\n".join(lines) + "\n"
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as handle:
            temporary = handle.name
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except OSError as exc:
        raise ConfigError(f"Could not save configuration {target}: {exc}") from exc
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return target
