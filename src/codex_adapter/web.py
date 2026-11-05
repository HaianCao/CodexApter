from __future__ import annotations

import copy
import json
import os
import secrets
import threading
import webbrowser
from http import HTTPStatus
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .agent_models import validate_agent_models
from .catalog import _identity, cached_models, fetch_models, store_catalog
from .config import (
    Config,
    Discovery,
    Provider,
    SecretReference,
    config_path,
    normalize_protocol,
    save,
    validate_auth_mode,
    validate_base_url,
    validate_capability_settings,
    validate_client_tool_search,
    validate_public_reasoning,
    validate_system_models,
)
from .credentials import env_file_path, remove_token, resolve_token, store_token
from .errors import AdapterError, ConfigError
from .gateway_options import validate_gateway_limits, validate_gateway_runtime
from .http_deadline import Deadline
from .launcher import launch, preflight
from .settings_store import SettingsGuard, atomic_bytes
from .web_http import SetupHTTPServer as ThreadingHTTPServer
from .web_http import SetupRequestHandler as BaseHTTPRequestHandler
from .web_security import local_request, token_matches


def public_config(config: Config) -> dict:
    return {
        "defaults": vars(config.defaults),
        "providers": [
            {
                "id": provider.id,
                "display_name": provider.display_name,
                "base_url": provider.base_url,
                "protocol": provider.protocol,
                "auth_mode": provider.auth_mode,
                "client_tool_search": provider.client_tool_search,
                "execution_mode": provider.execution_mode,
                "gateway_max_output_tokens": provider.gateway_max_output_tokens,
                "upstream_reasoning": provider.upstream_reasoning,
                "messages_system_models": provider.messages_system_models,
                "capabilities_file": provider.capabilities_file,
                "require_verified_capabilities": provider.require_verified_capabilities,
                "public_reasoning": provider.public_reasoning,
                "gateway_limits": provider.gateway_limits,
                "gateway_runtime": provider.gateway_runtime,
                "agent_models": provider.agent_models,
                "auth_secret_ref": provider.auth_secret_ref,
                "default_model": provider.default_model,
                "discovery": vars(provider.discovery),
            }
            for provider in config.providers.values()
        ],
        "secrets": [{"name": ref.name, "env_var": ref.env_var} for ref in config.secrets.values()],
    }


def _validate_provider_id(value: str) -> None:
    import re

    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", value):
        raise ConfigError("Provider ID must use lowercase letters, digits, '_' or '-'.")


def _default_secret_names(provider_id: str) -> tuple[str, str]:
    """Derive internal secret identifiers from a validated provider ID."""
    env_var = "".join(char if char.isalnum() else "_" for char in provider_id).upper() + "_TOKEN"
    return f"{provider_id}-main", env_var


def _new_secret_names(config: Config, provider_id: str) -> tuple[str, str]:
    secret_ref, secret_env = _default_secret_names(provider_id)
    if secret_ref in config.secrets or any(
        ref.env_var and ref.env_var.casefold() == secret_env.casefold()
        for ref in config.secrets.values()
    ):
        raise ConfigError("This provider ID conflicts with an existing API-key name. Choose another provider ID.")
    return secret_ref, secret_env


def _remove_provider_config(config: Config, provider_id: str) -> str | None:
    provider = config.providers[provider_id]
    secret_ref = provider.auth_secret_ref
    del config.providers[provider_id]
    if config.defaults.provider == provider_id:
        config.defaults.provider = config.defaults.model = None
    for alias in [name for name, model in config.models.items() if model.provider == provider_id]:
        del config.models[alias]
    if secret_ref and not any(item.auth_secret_ref == secret_ref for item in config.providers.values()):
        secret = config.secrets.pop(secret_ref, None)
        if secret and secret.env_var and not any(item.env_var == secret.env_var for item in config.secrets.values()):
            return secret.env_var
    return None


def persist_change(config: Config, updated: Config, env_var=None, token=None) -> None:
    """Commit memory only after disk, restoring exact secret bytes on failure."""
    from .config import validate_secret_names
    validate_secret_names(updated)
    target = env_file_path(config)
    previous = target.read_bytes() if env_var and token is not None and target.exists() else None
    process_value = os.environ.get(env_var) if env_var else None
    updated.env_file = target
    try:
        if env_var and token is not None:
            if token:
                store_token(env_var, token, config=updated)
            else:
                remove_token(env_var, config=updated)
        save(updated)
    except Exception:
        if env_var and token is not None:
            try:
                if previous is None:
                    target.unlink(missing_ok=True)
                else:
                    atomic_bytes(target, previous)
            except OSError:
                raise AdapterError("Saving settings and restoring the API key failed. Check local storage permissions.") from None
            finally:
                if process_value is None:
                    os.environ.pop(env_var, None)
                else:
                    os.environ[env_var] = process_value
        raise
    config.defaults, config.providers, config.secrets = updated.defaults, updated.providers, updated.secrets
    config.models, config.env_file = updated.models, updated.env_file


def serve(config: Config) -> int:
    session_token = secrets.token_urlsafe(32)
    run_request: tuple[Provider, str, str] | None = None
    state_lock = threading.RLock()
    guard = SettingsGuard(config.path or config_path())
    refreshes = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _authorized(self) -> bool:
            return local_request(self.headers, server.server_port) and token_matches(self.headers.get("X-Codex-Apter-Token", ""), session_token)

        def _json(self, value, status: int = HTTPStatus.OK):
            body = json.dumps(value, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 1 or length > 64_000:
                    raise AdapterError("Request body is too large.")
                value = json.loads(self.rfile.read(length))
            except RecursionError:
                raise AdapterError("JSON request nesting is too deep.") from None
            except (ValueError, json.JSONDecodeError) as exc:
                raise AdapterError("Invalid JSON request.") from exc
            if not isinstance(value, dict):
                raise AdapterError("Request body must be a JSON object.")
            return value

        def _provider(self, body: dict) -> Provider:
            provider = config.providers.get(str(body.get("provider", "")))
            if not provider:
                raise AdapterError("Choose a configured provider.")
            return provider

        def do_GET(self):
            if not local_request(self.headers, server.server_port):
                self._json({"error": "Setup is local-only."}, HTTPStatus.FORBIDDEN)
                return
            parsed = urlparse(self.path)
            if parsed.path == "/":
                supplied = parse_qs(parsed.query).get("token", [""])[0]
                if not token_matches(supplied, session_token):
                    self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                    return
                page = _HTML.replace("__SESSION_TOKEN__", session_token).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'none'")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(page)))
                self.end_headers()
                self.wfile.write(page)
                return
            if parsed.path in {
                "/assets/CodexApter_white.png",
                "/assets/CodexApter_black.png",
                "/assets/CodexApter_white_icon.png",
                "/assets/CodexApter_black_icon.png",
            }:
                asset_name = parsed.path.rsplit("/", 1)[-1]
                packaged_asset = Path(__file__).with_name("assets") / asset_name
                project_asset = Path(__file__).resolve().parents[2] / "assets" / asset_name
                asset_path = packaged_asset if packaged_asset.is_file() else project_asset
                if not asset_path.is_file():
                    self._json({"error": "Brand asset not found."}, HTTPStatus.NOT_FOUND)
                    return
                payload = asset_path.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=3600")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                try:
                    self.wfile.write(payload)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass  # A closed tab can cancel an in-flight brand asset.
                return
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            if parsed.path == "/api/config":
                with state_lock:
                    self._json(public_config(config))
                return
            if parsed.path == "/api/diagnostics":
                from .gateway_options import diagnostic_report

                provider = config.providers.get(parse_qs(parsed.query).get("provider", [""])[0])
                if not provider:
                    self._json({"error": "Unknown provider"}, HTTPStatus.NOT_FOUND)
                    return
                self._json(diagnostic_report(config.path or config_path(), provider))
                return
            if parsed.path == "/api/models":
                provider_id = parse_qs(parsed.query).get("provider", [""])[0]
                provider = config.providers.get(provider_id)
                if not provider:
                    self._json({"error": "Unknown provider"}, HTTPStatus.NOT_FOUND)
                    return
                models, stale = cached_models(provider, config=config)
                from .catalog import cached_model_names

                self._json({"models": models, "stale": stale, "model_names": cached_model_names(config, provider, models)})
                return
            if parsed.path == "/api/model-status":
                from .model_selection import model_status

                query = parse_qs(parsed.query)
                provider = config.providers.get(query.get("provider", [""])[0])
                model = query.get("model", [""])[0]
                if provider is None or len(model) > 512:
                    self._json({"error": "Choose a configured provider and model."}, HTTPStatus.BAD_REQUEST)
                    return
                self._json(model_status(config, provider, model))
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            try:
                self.connection.settimeout(15)
                body = self._body()
                if urlparse(self.path).path == "/api/models/refresh":
                    self._refresh(body)
                    return
                with state_lock, guard.edit():
                    self._post(body)
            except TimeoutError:
                self._json({"error": "Setup request timed out."}, HTTPStatus.REQUEST_TIMEOUT)
            except OSError:
                self._json({"error": "Could not save settings. Check local storage permissions."}, HTTPStatus.INTERNAL_SERVER_ERROR)
            except (AdapterError, ValueError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def _refresh(self, body):
            with state_lock, guard.edit():
                snapshot = copy.deepcopy(config)
                provider = snapshot.providers.get(str(body.get("provider", "")))
                if provider is None:
                    raise AdapterError("Choose a configured provider.")
                credential = resolve_token(snapshot, provider, required=False)
                identity = _identity(provider, snapshot, token=credential)
                budget = Deadline(15)
                refreshes.add(budget)
            entries = []
            try:
                with budget:
                    models = fetch_models(snapshot, provider, cache_sink=entries.append, budget=budget, token=credential)
                with state_lock, guard.edit():
                    current = config.providers.get(provider.id)
                    if run_request is not None or current != provider or _identity(current, config) != identity or (entries and entries[0]["identity"] != identity):
                        raise AdapterError("Provider settings changed during refresh. Refresh models again.")
                    if entries:
                        store_catalog(provider.id, entries[0])
                from .catalog import cached_model_names

                self._json({"models": models, "stale": False, "model_names": cached_model_names(config, current, models)})
            finally:
                with state_lock:
                    refreshes.discard(budget)

        def _post(self, body):
            nonlocal run_request
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            try:
                path = urlparse(self.path).path
                if path == "/api/provider":
                    provider_id = str(body.get("id", "")).strip()
                    _validate_provider_id(provider_id)
                    display_name = str(body.get("display_name", "")).strip()
                    base_url = validate_base_url(body.get("base_url", ""))
                    current = config.providers.get(provider_id)
                    protocol = normalize_protocol(str(body.get("protocol", current.protocol if current else "responses")))
                    if protocol not in {"responses", "chat", "messages"}:
                        raise ConfigError("Choose Responses, Chat Completions, or Messages.")
                    execution_mode = str(body.get("execution_mode", current.execution_mode if current else "direct"))
                    if execution_mode not in {"direct", "gateway"} or (protocol != "responses" and execution_mode != "gateway"):
                        raise ConfigError("Chat Completions and Messages providers require gateway mode.")
                    max_tokens = body.get("gateway_max_output_tokens", current.gateway_max_output_tokens if current else 4096)
                    system_models = validate_system_models(body.get("messages_system_models", current.messages_system_models if current else []), protocol, execution_mode)
                    capabilities_file, require_verified = validate_capability_settings(body.get("capabilities_file", current.capabilities_file if current else ""), body.get("require_verified_capabilities", current.require_verified_capabilities if current else False), execution_mode)
                    public_reasoning = validate_public_reasoning(body.get("public_reasoning", current.public_reasoning if current else False), protocol, execution_mode)
                    upstream_reasoning = str(body.get("upstream_reasoning", current.upstream_reasoning if current else "auto"))
                    if upstream_reasoning not in {"auto", "provider_default", "disabled", "openrouter_disabled"} or (execution_mode != "gateway" and upstream_reasoning not in {"auto", "provider_default"}):
                        raise ConfigError("Choose a valid upstream reasoning policy; disabling requires gateway mode.")
                    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0:
                        raise ConfigError("Gateway token limit must be a positive integer.")
                    if not display_name:
                        raise ConfigError("Display name is required.")
                    # Keep existing custom mappings intact, but new providers get
                    # predictable names without asking users to manage internals.
                    if current and current.auth_secret_ref:
                        secret_ref = current.auth_secret_ref
                        secret = config.secrets.get(secret_ref)
                        secret_env = secret.env_var if secret else _default_secret_names(provider_id)[1]
                    else:
                        # Distinct IDs such as a-b/a_b can derive the same env
                        # name. Never implicitly share/overwrite an existing key.
                        secret_ref, secret_env = _new_secret_names(config, provider_id)
                    updated = copy.deepcopy(config)
                    updated.secrets[secret_ref] = SecretReference(secret_ref, env_var=secret_env)
                    updated.providers[provider_id] = Provider(
                        id=provider_id,
                        display_name=display_name,
                        base_url=base_url,
                        protocol=protocol,
                        auth_mode=validate_auth_mode(body.get("auth_mode", current.auth_mode if current else "bearer"), execution_mode),
                        client_tool_search=validate_client_tool_search(body.get("client_tool_search", current.client_tool_search if current else "reject"), protocol, execution_mode),
                        execution_mode=execution_mode,
                        auth_secret_ref=secret_ref,
                        default_model=str(body.get("default_model", "")).strip() or (current.default_model if current else None),
                        discovery=copy.deepcopy(current.discovery) if current else Discovery(enabled=True),
                        gateway_max_output_tokens=max_tokens,
                        upstream_reasoning=upstream_reasoning,
                        messages_system_models=system_models,
                        capabilities_file=capabilities_file,
                        require_verified_capabilities=require_verified,
                        public_reasoning=public_reasoning,
                        gateway_limits=validate_gateway_limits(body.get("gateway_limits", current.gateway_limits if current else {}), execution_mode),
                        gateway_runtime=validate_gateway_runtime(body.get("gateway_runtime", current.gateway_runtime if current else {}), execution_mode),
                        agent_models=validate_agent_models(body.get("agent_models", current.agent_models if current else {"policy": "selected"})),
                    )
                    api_key = str(body.get("api_key", ""))
                    if "discovery" in body:
                        if not isinstance(body["discovery"], bool):
                            raise ConfigError("Discovery must be a boolean.")
                        updated.providers[provider_id].discovery.enabled = body["discovery"]
                    persist_change(config, updated, secret_env, api_key or None)
                    self._json(public_config(config))
                    return
                if path == "/api/default":
                    provider = self._provider(body)
                    model = str(body.get("model", "")).strip()
                    if not model:
                        raise ConfigError("Choose a model or enter a model ID.")
                    updated = copy.deepcopy(config)
                    updated.defaults.provider = provider.id
                    updated.defaults.model = model
                    updated.defaults.reasoning_effort = str(body.get("reasoning", "medium"))
                    if updated.defaults.reasoning_effort not in {"low", "medium", "high"}:
                        raise ConfigError("Reasoning effort must be low, medium, or high.")
                    persist_change(config, updated)
                    self._json(public_config(config))
                    return
                if path == "/api/run":
                    provider = self._provider(body)
                    model = str(body.get("model", "")).strip()
                    reasoning = str(body.get("reasoning", "medium"))
                    if not model:
                        raise ConfigError("Choose a model or enter a model ID.")
                    if reasoning not in {"low", "medium", "high"}:
                        raise ConfigError("Reasoning effort must be low, medium, or high.")
                    # Run is session-only; only /api/default persists defaults.
                    preflight(config, provider, model, reasoning)
                    run_request = (provider, model, reasoning)
                    self._json({"started": True})
                    threading.Thread(target=server.shutdown, daemon=True).start()
                    return
                self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)
            except (AdapterError, KeyError, TypeError, ValueError) as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        def do_DELETE(self):
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            try:
                with state_lock, guard.edit():
                    self._delete()
            except (OSError, AdapterError, ValueError):
                self._json({"error": "Could not remove provider; settings were not committed. Reopen setup if settings changed elsewhere."}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _delete(self):
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            provider_id = parse_qs(urlparse(self.path).query).get("provider", [""])[0]
            if provider_id not in config.providers:
                self._json({"error": "Unknown provider"}, HTTPStatus.NOT_FOUND)
                return
            updated_config = copy.deepcopy(config)
            env_var_to_remove = _remove_provider_config(updated_config, provider_id)
            try:
                persist_change(config, updated_config, env_var_to_remove, "" if env_var_to_remove else None)
            except (OSError, ConfigError) as exc:
                self._json({"error": f"Could not save provider changes: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            config.providers = updated_config.providers
            config.secrets = updated_config.secrets
            config.models = updated_config.models
            config.defaults = updated_config.defaults
            self._json(public_config(config))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/?token={session_token}"
    print("Opened CodexApter setup in your browser. Press Ctrl+C here to stop the local server.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        with state_lock:
            pending = tuple(refreshes)
        for budget in pending:
            budget.cancel()
        server.server_close()
    if run_request:
        provider, model, reasoning = run_request
        # Hand the same terminal from the setup server to the foreground Codex
        # CLI. Wait here so setup cannot exit and take the TUI's console with it.
        return launch(config, provider, model, reasoning, [])
    return 0


_HTML = Path(__file__).with_name("web_template.html").read_text(encoding="utf-8")
