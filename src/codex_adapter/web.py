from __future__ import annotations

import copy
import json
import secrets
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .catalog import cached_models, fetch_models
from .config import Config, Discovery, Provider, SecretReference, save, validate_base_url, validate_system_models, validate_capability_settings, validate_public_reasoning
from .credentials import env_file_path, remove_token, store_token
from .errors import AdapterError, ConfigError
from .launcher import launch, preflight


def public_config(config: Config) -> dict:
    return {
        "defaults": vars(config.defaults),
        "providers": [
            {
                "id": provider.id,
                "display_name": provider.display_name,
                "base_url": provider.base_url,
                "protocol": provider.protocol,
                "execution_mode": provider.execution_mode,
                "gateway_max_output_tokens": provider.gateway_max_output_tokens,
                "upstream_reasoning": provider.upstream_reasoning,
                "messages_system_models": provider.messages_system_models,
                "capabilities_file": provider.capabilities_file,
                "require_verified_capabilities": provider.require_verified_capabilities,
                "public_reasoning": provider.public_reasoning,
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


def serve(config: Config) -> int:
    session_token = secrets.token_urlsafe(32)
    run_request: tuple[Provider, str, str] | None = None

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def _authorized(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Codex-Apter-Token", ""), session_token)

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
                if length > 64_000:
                    raise AdapterError("Request body is too large.")
                value = json.loads(self.rfile.read(length))
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
            parsed = urlparse(self.path)
            if parsed.path == "/":
                # The token remains in the URL only for the initial local page load;
                # no configuration or secret is embedded in the HTML response.
                page = _HTML.replace("__SESSION_TOKEN__", session_token).encode("utf-8")
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
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
                self.wfile.write(payload)
                return
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            if parsed.path == "/api/config":
                self._json(public_config(config))
                return
            if parsed.path == "/api/models":
                provider_id = parse_qs(parsed.query).get("provider", [""])[0]
                provider = config.providers.get(provider_id)
                if not provider:
                    self._json({"error": "Unknown provider"}, HTTPStatus.NOT_FOUND)
                    return
                models, stale = cached_models(provider)
                self._json({"models": models, "stale": stale})
                return
            self._json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

        def do_POST(self):
            nonlocal run_request
            if not self._authorized():
                self._json({"error": "Unauthorized"}, HTTPStatus.FORBIDDEN)
                return
            try:
                body = self._body()
                path = urlparse(self.path).path
                if path == "/api/provider":
                    provider_id = str(body.get("id", "")).strip()
                    _validate_provider_id(provider_id)
                    display_name = str(body.get("display_name", "")).strip()
                    base_url = validate_base_url(str(body.get("base_url", "")).strip())
                    current = config.providers.get(provider_id)
                    protocol = str(body.get("protocol", current.protocol if current else "responses"))
                    if protocol not in {"responses", "chat_completions", "anthropic_messages"}:
                        raise ConfigError("Choose Responses, Chat Completions, or Messages.")
                    execution_mode = str(body.get("execution_mode", current.execution_mode if current else "direct"))
                    if execution_mode not in {"direct", "gateway"} or (protocol != "responses" and execution_mode != "gateway"):
                        raise ConfigError("Chat Completions and Messages providers require gateway mode.")
                    max_tokens = body.get("gateway_max_output_tokens", current.gateway_max_output_tokens if current else 4096)
                    system_models = validate_system_models(body.get("messages_system_models", current.messages_system_models if current else []), protocol, execution_mode)
                    capabilities_file, require_verified = validate_capability_settings(body.get("capabilities_file", current.capabilities_file if current else ""), body.get("require_verified_capabilities", current.require_verified_capabilities if current else False), execution_mode)
                    public_reasoning = validate_public_reasoning(body.get("public_reasoning", current.public_reasoning if current else False), protocol, execution_mode)
                    upstream_reasoning = str(body.get("upstream_reasoning", current.upstream_reasoning if current else "provider_default"))
                    if upstream_reasoning not in {"provider_default", "disabled", "openrouter_disabled"} or (execution_mode != "gateway" and upstream_reasoning != "provider_default"):
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
                        secret_ref, secret_env = _default_secret_names(provider_id)
                    config.secrets[secret_ref] = SecretReference(secret_ref, env_var=secret_env)
                    config.providers[provider_id] = Provider(
                        id=provider_id,
                        display_name=display_name,
                        base_url=base_url,
                        protocol=protocol,
                        execution_mode=execution_mode,
                        auth_secret_ref=secret_ref,
                        default_model=str(body.get("default_model", "")).strip() or (current.default_model if current else None),
                        discovery=Discovery(enabled=bool(body.get("discovery", True))),
                        gateway_max_output_tokens=max_tokens,
                        upstream_reasoning=upstream_reasoning,
                        messages_system_models=system_models,
                        capabilities_file=capabilities_file,
                        require_verified_capabilities=require_verified,
                        public_reasoning=public_reasoning,
                    )
                    api_key = str(body.get("api_key", ""))
                    if api_key:
                        store_token(secret_env, api_key, config=config)
                    config.env_file = env_file_path(config)
                    save(config)
                    self._json(public_config(config))
                    return
                if path == "/api/default":
                    provider = self._provider(body)
                    model = str(body.get("model", "")).strip()
                    if not model:
                        raise ConfigError("Choose a model or enter a model ID.")
                    config.defaults.provider = provider.id
                    config.defaults.model = model
                    config.defaults.reasoning_effort = str(body.get("reasoning", "medium"))
                    if config.defaults.reasoning_effort not in {"low", "medium", "high"}:
                        raise ConfigError("Reasoning effort must be low, medium, or high.")
                    config.env_file = env_file_path(config)
                    save(config)
                    self._json(public_config(config))
                    return
                if path == "/api/models/refresh":
                    provider = self._provider(body)
                    self._json({"models": fetch_models(config, provider), "stale": False})
                    return
                if path == "/api/run":
                    provider = self._provider(body)
                    model = str(body.get("model", "")).strip()
                    reasoning = str(body.get("reasoning", "medium"))
                    if not model:
                        raise ConfigError("Choose a model or enter a model ID.")
                    if reasoning not in {"low", "medium", "high"}:
                        raise ConfigError("Reasoning effort must be low, medium, or high.")
                    # Show executable, credential, or gateway failures before
                    # persisting defaults and closing the setup page.
                    preflight(config, provider, model, reasoning)
                    config.defaults.provider, config.defaults.model, config.defaults.reasoning_effort = provider.id, model, reasoning
                    config.env_file = env_file_path(config)
                    save(config)
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
            provider_id = parse_qs(urlparse(self.path).query).get("provider", [""])[0]
            if provider_id not in config.providers:
                self._json({"error": "Unknown provider"}, HTTPStatus.NOT_FOUND)
                return
            updated_config = copy.deepcopy(config)
            env_var_to_remove = _remove_provider_config(updated_config, provider_id)
            try:
                save(updated_config)
            except (OSError, ConfigError) as exc:
                self._json({"error": f"Could not save provider changes: {exc}"}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            config.providers = updated_config.providers
            config.secrets = updated_config.secrets
            config.models = updated_config.models
            config.defaults = updated_config.defaults
            if env_var_to_remove:
                try:
                    remove_token(env_var_to_remove, config=config)
                except OSError:
                    self._json({"error": "Provider was removed, but its saved API key could not be deleted from the .env file."}, HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
            self._json(public_config(config))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    url = f"http://127.0.0.1:{server.server_port}/"
    print("Opened CodexApter setup in your browser. Press Ctrl+C here to stop the local server.")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    if run_request:
        provider, model, reasoning = run_request
        # Hand the same terminal from the setup server to the foreground Codex
        # CLI. Wait here so setup cannot exit and take the TUI's console with it.
        return launch(config, provider, model, reasoning, [])
    return 0


_HTML = Path(__file__).with_name("web_template.html").read_text(encoding="utf-8")
