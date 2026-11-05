"""Explicit, per-launch external MCP configuration; never global registration."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .toml_text import toml_value


def load_servers(value: str | None) -> dict:
    if not value:
        return {}
    try:
        with Path(value).expanduser().open("rb") as stream:
            raw = stream.read(65537)
        if len(raw) > 65536:
            raise ValueError
        document = json.loads(raw)
        if not isinstance(document, dict) or set(document) != {"mcpServers"}:
            raise ValueError
        servers = document["mcpServers"]
        if not isinstance(servers, dict) or not servers or len(servers) > 16:
            raise ValueError
        for name, server in servers.items():
            if not re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{0,63}", name) or not isinstance(server, dict):
                raise ValueError
            if set(server) - {"command", "args", "env_vars"}:
                raise ValueError
            command = server.get("command")
            if not isinstance(command, str) or "\0" in command or not Path(command).is_absolute() or not Path(command).is_file():
                raise ValueError
            args = server.get("args", [])
            names = server.get("env_vars", [])
            if not isinstance(args, list) or len(args) > 128 or any(not isinstance(arg, str) or "\0" in arg for arg in args):
                raise ValueError
            if not isinstance(names, list) or len(names) > 64 or any(not isinstance(key, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key) for key in names):
                raise ValueError
        return servers
    except (OSError, ValueError, TypeError, RecursionError, RuntimeError):
        raise ValueError("Invalid session MCP config: use mcpServers with absolute executable command, args and optional env_vars names (maximum 64 KiB). Inline env values and remote URLs are not supported.") from None


def codex_overrides(servers: dict) -> list[str]:
    settings = []
    for name, server in servers.items():
        # Names were restricted to safe bare-key characters above.
        prefix = f"mcp_servers.{name}"
        settings.append(f"{prefix}.required=true")
        for key in ("command", "args", "env_vars"):
            if key in server:
                settings.append(f"{prefix}.{key}={toml_value(server[key], ensure_ascii=True)}")
    return settings
