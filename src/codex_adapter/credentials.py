from __future__ import annotations

import json
import os
from pathlib import Path

from .config import Provider, Config, config_path
from .errors import CredentialError


def env_file_path(config: Config | None = None) -> Path:
    configured = config.env_file if config else None
    override = os.environ.get("CODEX_AI_ENV_FILE")
    if override:
        return Path(override).expanduser()
    if configured:
        return configured.expanduser()
    source_root = Path(__file__).resolve().parents[2]
    if (source_root / "pyproject.toml").is_file():
        return source_root / ".env"
    stable_config_path = config.path if config and config.path else config_path()
    return stable_config_path.expanduser().parent / ".env"


def resolve_token(config: Config, provider: Provider, *, required: bool = True) -> str | None:
    if not provider.auth_secret_ref:
        return None
    reference = config.secrets.get(provider.auth_secret_ref)
    if not reference or not reference.env_var:
        raise CredentialError(f"Provider '{provider.id}' has an invalid secret reference.")
    value = os.environ.get(reference.env_var) or _read_dotenv(env_file_path(config)).get(reference.env_var)
    if not value and required:
        raise CredentialError(f"Missing credential '{reference.env_var}' for provider '{provider.id}'.")
    return value


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                value = value.strip()
                if value.startswith('"'):
                    try:
                        decoded = json.loads(value)
                        if isinstance(decoded, str):
                            value = decoded
                    except json.JSONDecodeError:
                        pass
                elif len(value) >= 2 and value[0] == value[-1] == "'":
                    value = value[1:-1]
                values[key.strip()] = value
    except FileNotFoundError:
        pass
    return values


def store_token(env_var: str, token: str, path: Path | None = None, *, config: Config | None = None) -> None:
    """Store a token in a gitignored project .env file, never in TOML or logs."""
    if not token:
        return
    if "\n" in token or "\r" in token or "\0" in token:
        raise CredentialError("API key must be a single line.")
    target = path or env_file_path(config)
    values = _read_dotenv(target)
    values[env_var] = token
    target.write_text("".join(f"{key}={json.dumps(value, ensure_ascii=False)}\n" for key, value in sorted(values.items())), encoding="utf-8")
    os.environ[env_var] = token


def remove_token(env_var: str, path: Path | None = None, *, config: Config | None = None) -> None:
    """Remove one API key from the configured dotenv file and current process."""
    target = path or env_file_path(config)
    values = _read_dotenv(target)
    values.pop(env_var, None)
    if target.exists():
        if values:
            target.write_text("".join(f"{key}={json.dumps(value, ensure_ascii=False)}\n" for key, value in sorted(values.items())), encoding="utf-8")
        else:
            target.unlink()
    os.environ.pop(env_var, None)
