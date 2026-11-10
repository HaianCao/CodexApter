from __future__ import annotations

import os
import re
import shutil
import tempfile
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

try:
    from protoverta.error_classes import RetryPolicy
except ImportError:  # A broken install must still open the setup page.
    RetryPolicy = None  # type: ignore[assignment,misc]

from .errors import ConfigError
from .toml_text import toml_value

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
# Protocol names match Protoverta and ClaudApter. Older CodexApter configs used
# the longer names; they are accepted on input and saved in the shared form.
_PROTOCOLS = {"responses", "chat", "messages"}
_LEGACY_PROTOCOLS = {"chat_completions": "chat", "anthropic_messages": "messages"}
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
            existing = {
                line.split("=", 1)[0].strip() for line in new_env.read_text(encoding="utf-8").splitlines() if "=" in line and not line.lstrip().startswith("#")
            }
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
            new_line = f"env_file = {toml_value(str(new_env))}"
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


# Messages requires an explicit output cap; 4096 truncated long code edits.
DEFAULT_GATEWAY_MAX_OUTPUT_TOKENS = 16384


def validate_model_limits(value: Any) -> dict:
    """Codex session override for a provider model's real context window."""
    if not isinstance(value, dict) or set(value) - {"context_window"}:
        raise ConfigError("model_limits accepts context_window only.")
    window = value.get("context_window")
    if window is not None and (type(window) is not int or not 1 <= window <= 100_000_000):
        raise ConfigError("model_limits.context_window must be a positive whole token count.")
    return dict(value)


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
    gateway_max_output_tokens: int = DEFAULT_GATEWAY_MAX_OUTPUT_TOKENS
    upstream_reasoning: str = "provider_default"
    messages_system_models: list[str] = field(default_factory=list)
    capabilities_file: str = ""
    require_verified_capabilities: bool = False
    public_reasoning: bool = False
    gateway_limits: dict = field(default_factory=dict)
    auth_mode: str = "bearer"
    client_tool_search: str = "reject"
    gateway_runtime: dict = field(default_factory=dict)
    agent_models: dict = field(default_factory=dict)
    model_limits: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.protocol = normalize_protocol(self.protocol)


def normalize_protocol(value: Any) -> Any:
    """Map a legacy CodexApter protocol name to the shared name; leave others unchanged."""
    return _LEGACY_PROTOCOLS.get(value, value) if isinstance(value, str) else value


def validate_client_tool_search(value: Any, protocol: str, mode: str) -> str:
    if value not in ("reject", "function"):
        raise ConfigError("Client tool search must be reject or function.")
    if value == "function" and (mode != "gateway" or protocol == "responses"):
        raise ConfigError("Client search emulation requires a Chat or Messages gateway.")
    return value


def validate_auth_mode(value: Any, mode: str) -> str:
    if value not in ("bearer", "api_key"):
        raise ConfigError("Authentication must be bearer or api_key.")
    if value == "api_key" and mode != "gateway":
        raise ConfigError("x-api-key authentication requires gateway mode.")
    return value


def validate_public_reasoning(value: Any, protocol: str, mode: str) -> bool:
    if not isinstance(value, bool) or (value and (protocol != "chat" or mode != "gateway")):
        raise ConfigError("public_reasoning must be boolean and requires a Chat Completions gateway provider.")
    return value


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
    if value and (protocol != "messages" or mode != "gateway"):
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
    # The [retry] table as written; see retry_policy(). Empty means defaults.
    retry: dict = field(default_factory=dict)

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
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError("Provider base URL must be text without whitespace or control characters.")
    try:
        parsed = urlsplit(value)
        port = parsed.port  # Force validation of a malformed port number.
    except ValueError as exc:
        raise ConfigError("Provider base URL is invalid.") from exc
    if not parsed.hostname or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
        raise ConfigError("Provider base URL must be an HTTPS URL without credentials, query, or fragment.")
    if port is not None and port < 1:
        raise ConfigError("Provider base URL port must be between 1 and 65535.")
    if parsed.scheme.lower() != "https" and not (parsed.scheme.lower() == "http" and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}):
        raise ConfigError("Provider base URL must use HTTPS (HTTP is allowed only for localhost/loopback).")
    if parsed.path.rstrip("/").endswith(("/responses", "/messages", "/chat/completions")):
        raise ConfigError("Enter the base URL, for example https://provider.example/v1, without the endpoint suffix.")
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
        _string(item.get("base_url"), f"providers.{name}.base_url", True)
        base_url = validate_base_url(item["base_url"])
        protocol = normalize_protocol(_string(item.get("protocol", "responses"), f"providers.{name}.protocol", required=True))
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
            response_format=_string(
                discovery_raw.get("response_format", "openai_models_list"), f"providers.{name}.model_discovery.response_format", required=True
            ),
            cache_ttl_seconds=_positive_integer(discovery_raw.get("cache_ttl_seconds", 3600), f"providers.{name}.model_discovery.cache_ttl_seconds"),
        )
        if discovery.method != "GET" or discovery.response_format != "openai_models_list" or not discovery.path.startswith("/") or "://" in discovery.path:
            raise ConfigError(f"providers.{name}.model_discovery only permits a relative GET path.")
        display_name = _string(item.get("display_name", name), f"providers.{name}.display_name", required=True)
        gateway_max_output_tokens = _positive_integer(
            item.get("gateway_max_output_tokens", DEFAULT_GATEWAY_MAX_OUTPUT_TOKENS), f"providers.{name}.gateway_max_output_tokens"
        )
        upstream_reasoning = item.get("upstream_reasoning", "auto")
        if upstream_reasoning not in ("auto", "provider_default", "disabled", "openrouter_disabled") or (
            mode != "gateway" and upstream_reasoning not in ("auto", "provider_default")
        ):
            raise ConfigError(f"providers.{name}.upstream_reasoning requires a valid policy and gateway mode.")
        system_models = validate_system_models(item.get("messages_system_models", []), protocol, mode)
        capabilities_file, require_verified = validate_capability_settings(
            item.get("capabilities_file", ""), item.get("require_verified_capabilities", False), mode
        )
        public_reasoning = validate_public_reasoning(item.get("public_reasoning", False), protocol, mode)
        providers[name] = Provider(
            name,
            display_name,
            base_url,
            protocol,
            mode,
            secret_ref,
            _string(item.get("default_model"), f"providers.{name}.default_model"),
            discovery,
            gateway_max_output_tokens,
            upstream_reasoning,
            system_models,
            capabilities_file,
            require_verified,
            public_reasoning,
        )
        from .gateway_options import validate_gateway_limits

        providers[name].gateway_limits = validate_gateway_limits(item.get("gateway_limits", {}), mode)
        # Preserve old Messages configurations which previously used x-api-key.
        providers[name].auth_mode = validate_auth_mode(item.get("auth_mode", "api_key" if protocol == "messages" else "bearer"), mode)
        providers[name].client_tool_search = validate_client_tool_search(item.get("client_tool_search", "reject"), protocol, mode)
        from .gateway_options import validate_gateway_runtime

        providers[name].gateway_runtime = validate_gateway_runtime(item.get("gateway_runtime", {}), mode)
        from .agent_models import validate_agent_models

        providers[name].agent_models = validate_agent_models(item.get("agent_models", {}))
        providers[name].model_limits = validate_model_limits(item.get("model_limits", {}))
    models: dict[str, ModelAlias] = {}
    for name, item in models_raw.items():
        _check_id(name, "Model alias")
        item = _table(item, f"models.{name}")
        provider = _string(item.get("provider"), f"models.{name}.provider", True)
        if provider not in providers:
            raise ConfigError(f"models.{name} references missing provider '{provider}'.")
        alias_reasoning = item.get("reasoning_effort")
        models[name] = ModelAlias(
            name,
            provider,
            _string(item.get("model_id"), f"models.{name}.model_id", True),
            _reasoning_effort(alias_reasoning, f"models.{name}.reasoning_effort") if alias_reasoning is not None else None,
        )
    configured_env_file = _string(raw.get("env_file"), "env_file")
    retry = raw.get("retry", {})
    if not isinstance(retry, dict):
        raise ConfigError("[retry] must be a TOML table.")
    config = Config(defaults, providers, secrets, models, source, Path(configured_env_file).expanduser() if configured_env_file else None, retry)
    if RetryPolicy is not None:  # Without the core, launching reports the repair step.
        retry_policy(config)
    validate_secret_names(config)
    if defaults.provider and defaults.provider not in providers:
        raise ConfigError(f"defaults.provider references missing provider '{defaults.provider}'.")
    return config


def _quote(value: str) -> str:
    return toml_value(value)


def retry_policy(config: Config):
    """The session's error-class retry policy (Protoverta ``RetryPolicy``)."""
    if RetryPolicy is None:
        raise ConfigError("CodexApter's Protoverta component is missing or broken. Run the installer command again to repair it.")
    try:
        return RetryPolicy.from_mapping(config.retry)
    except ValueError as exc:
        raise ConfigError(f"Invalid [retry] settings: {exc}") from None


def validate_secret_names(config: Config) -> None:
    """Require explicit sharing via one reference, not colliding env names."""
    owners = {}
    for name, ref in config.secrets.items():
        if not ref.env_var:
            continue
        normalized = ref.env_var.casefold()
        if normalized in owners:
            raise ConfigError(
                f"Secret references '{owners[normalized]}' and '{name}' use the same environment variable. Use one shared reference or distinct variable names."
            )
        owners[normalized] = name


def save(config: Config, path: Path | None = None) -> Path:
    validate_secret_names(config)
    target = path or config.path or config_path()
    lines = ["schema_version = 1", "", "[defaults]"]
    if config.env_file:
        lines.insert(1, f"env_file = {_quote(str(config.env_file))}")
    for key, value in (
        ("provider", config.defaults.provider),
        ("model", config.defaults.model),
        ("reasoning_effort", config.defaults.reasoning_effort),
        ("execution_mode", config.defaults.execution_mode),
    ):
        if value:
            lines.append(f"{key} = {_quote(value)}")
    if config.retry:
        # Written as the user set it (0.1.x wrote this table too).
        lines += ["", "[retry]"] + [f"{key} = {toml_value(config.retry[key])}" for key in sorted(config.retry)]
    for name, secret in sorted(config.secrets.items()):
        lines.extend(["", f"[secrets.{name}]", f"backend = {_quote(secret.backend)}", f"env_var = {_quote(secret.env_var or '')}"])
    for name, provider in sorted(config.providers.items()):
        validate_base_url(provider.base_url)
        from .gateway_options import (
            limits_toml,
            runtime_toml,
            validate_gateway_limits,
            validate_gateway_runtime,
        )

        limits = validate_gateway_limits(provider.gateway_limits, provider.execution_mode)
        runtime = validate_gateway_runtime(provider.gateway_runtime, provider.execution_mode)
        auth_mode = validate_auth_mode(provider.auth_mode, provider.execution_mode)
        search_policy = validate_client_tool_search(provider.client_tool_search, provider.protocol, provider.execution_mode)
        public_reasoning = validate_public_reasoning(provider.public_reasoning, provider.protocol, provider.execution_mode)
        capabilities_file, require_verified = validate_capability_settings(
            provider.capabilities_file, provider.require_verified_capabilities, provider.execution_mode
        )
        if provider.upstream_reasoning not in ("auto", "provider_default", "disabled", "openrouter_disabled") or (
            provider.execution_mode != "gateway" and provider.upstream_reasoning not in ("auto", "provider_default")
        ):
            raise ConfigError(f"providers.{name}.upstream_reasoning requires a valid policy and gateway mode.")
        lines.extend(
            [
                "",
                f"[providers.{name}]",
                f"display_name = {_quote(provider.display_name)}",
                f"base_url = {_quote(provider.base_url)}",
                f"protocol = {_quote(provider.protocol)}",
                f"execution_mode = {_quote(provider.execution_mode)}",
            ]
        )
        if provider.execution_mode == "gateway":
            lines.append(f"gateway_max_output_tokens = {provider.gateway_max_output_tokens}")
        lines.append(f"auth_mode = {_quote(auth_mode)}")
        if search_policy != "reject":
            lines.append(f"client_tool_search = {_quote(search_policy)}")
        if public_reasoning:
            lines.append("public_reasoning = true")
        lines.append(f"upstream_reasoning = {_quote(provider.upstream_reasoning)}")
        if limits:
            lines.append("gateway_limits = " + limits_toml(limits))
        if runtime:
            lines.append("gateway_runtime = " + runtime_toml(runtime))
        from .agent_models import agent_models_toml, validate_agent_models

        agents = validate_agent_models(provider.agent_models)
        if agents:
            lines.append("agent_models = " + agent_models_toml(agents))
        model_limits = validate_model_limits(provider.model_limits)
        if model_limits:
            lines.append("model_limits = { " + ", ".join(f"{key} = {model_limits[key]}" for key in sorted(model_limits)) + " }")
        if capabilities_file:
            lines.extend([f"capabilities_file = {_quote(capabilities_file)}", f"require_verified_capabilities = {str(require_verified).lower()}"])
        system_models = validate_system_models(provider.messages_system_models, provider.protocol, provider.execution_mode)
        if system_models:
            lines.append(f"messages_system_models = {toml_value(system_models)}")
        if provider.auth_secret_ref:
            lines.append(f"auth_secret_ref = {_quote(provider.auth_secret_ref)}")
        if provider.default_model:
            lines.append(f"default_model = {_quote(provider.default_model)}")
        d = provider.discovery
        lines.extend(
            [
                "",
                f"[providers.{name}.model_discovery]",
                f"enabled = {str(d.enabled).lower()}",
                f"path = {_quote(d.path)}",
                f"method = {_quote(d.method)}",
                f"response_format = {_quote(d.response_format)}",
                f"cache_ttl_seconds = {d.cache_ttl_seconds}",
            ]
        )
    for name, alias in sorted(config.models.items()):
        lines.extend(["", f"[models.{name}]", f"provider = {_quote(alias.provider)}", f"model_id = {_quote(alias.model_id)}"])
        if alias.reasoning_effort:
            lines.append(f"reasoning_effort = {_quote(alias.reasoning_effort)}")
    target.parent.mkdir(parents=True, exist_ok=True)
    content = "\n".join(lines) + "\n"
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", newline="\n", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False
        ) as handle:
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
