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
from .config import Config, Discovery, Provider, SecretReference, save, validate_base_url
from .credentials import env_file_path, remove_token, store_token
from .errors import AdapterError, ConfigError
from .launcher import start


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
                "/assets/CodexAI_white.png",
                "/assets/CodexAI_black.png",
                "/assets/CodexAI_white_icon.png",
                "/assets/CodexAI_black_icon.png",
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
                    if not display_name:
                        raise ConfigError("Display name is required.")
                    current = config.providers.get(provider_id)
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
                        protocol="responses",
                        execution_mode="direct",
                        auth_secret_ref=secret_ref,
                        default_model=str(body.get("default_model", "")).strip() or (current.default_model if current else None),
                        discovery=Discovery(enabled=bool(body.get("discovery", True))),
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
                    config.defaults.provider, config.defaults.model, config.defaults.reasoning_effort = provider.id, model, reasoning
                    config.env_file = env_file_path(config)
                    save(config)
                    start(config, provider, model, reasoning, [])
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


_HTML = Path(__file__).with_name("web_template.html").read_text(encoding="utf-8")
