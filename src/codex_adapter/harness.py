"""Version-scoped CLI compatibility; no model/provider capability inference."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import warnings
from functools import lru_cache
from pathlib import Path

_ESSENTIAL = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP"}


def parse_version(raw: bytes) -> tuple[int, int, int] | None:
    if len(raw) > 256:
        return None
    match = re.fullmatch(rb"codex-cli ([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})\s*", raw)
    return tuple(map(int, match.groups())) if match else None


def detect_version(prefix: list[str]) -> tuple[int, int, int] | None:
    # Refresh after an executable/npm entry changes; cache no credential or env.
    identity = []
    for part in prefix:
        try:
            stat = Path(part).stat()
            identity.append((stat.st_mtime_ns, stat.st_size))
        except OSError:
            identity.append(None)
    return _probe(tuple(prefix), tuple(identity))


@lru_cache(maxsize=32)
def _probe(prefix: tuple[str, ...], identity: tuple) -> tuple[int, int, int] | None:
    del identity  # Included in the cache key only.
    try:
        with tempfile.TemporaryDirectory(prefix="codex-ca-version-") as directory:
            root = Path(directory)
            env = {key: value for key, value in os.environ.items() if key.upper() in _ESSENTIAL}
            for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "CODEX_HOME", "CLAUDE_CONFIG_DIR"):
                target = root / key
                target.mkdir()
                env[key] = str(target)
            # A regular file avoids pipe deadlocks; inspect at most 257 bytes.
            # The trusted installed CLI owns its version command implementation.
            with (root / "version").open("w+b") as output:
                result = subprocess.run([*prefix, "--version"], env=env, cwd=root,
                                        stdin=subprocess.DEVNULL, stdout=output,
                                        stderr=subprocess.DEVNULL, timeout=3, check=False)
                output.seek(0)
                return parse_version(output.read(257)) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def cross_protocol_overrides(prefix: list[str]) -> list[str]:
    version = detect_version(prefix)
    settings = ['model_reasoning_summary="none"', 'features.multi_agent=false', 'web_search="disabled"']
    if version == (0, 166, 0):
        settings.append("agents.enabled=false")
    elif version != (0, 164, 0):
        warnings.warn("Codex CLI version is not verified for translated routes; retaining conservative compatibility settings. Subagents remain unverified.", RuntimeWarning, stacklevel=2)
    return settings
