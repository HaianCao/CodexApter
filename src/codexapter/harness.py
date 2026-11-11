"""Version-scoped CLI compatibility; no model/provider capability inference."""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import warnings
from functools import lru_cache
from pathlib import Path
from typing import TypeGuard

# A cold native CLI start (a large executable scanned by antivirus on first
# run) can take several seconds; 3 s silently disabled version-gated features.
VERSION_PROBE_SECONDS = 15
_ESSENTIAL = {"PATH", "PATHEXT", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "TEMP", "TMP"}


def parse_version(raw: bytes) -> tuple[int, int, int] | None:
    if len(raw) > 256:
        return None
    match = re.fullmatch(rb"codex-cli ([0-9]{1,5})\.([0-9]{1,5})\.([0-9]{1,5})\s*", raw)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def detect_version(prefix: list[str]) -> tuple[int, int, int] | None:
    # Refresh after an executable/npm entry changes; cache no credential or env.
    identity: list[tuple[int, int] | None] = []
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
                result = subprocess.run(
                    [*prefix, "--version"],
                    env=env,
                    cwd=root,
                    stdin=subprocess.DEVNULL,
                    stdout=output,
                    stderr=subprocess.DEVNULL,
                    timeout=VERSION_PROBE_SECONDS,
                    check=False,
                )
                output.seek(0)
                return parse_version(output.read(257)) if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


# Releases whose launcher settings were exercised end to end (loopback client
# smoke, tool loops). The schema-scoped settings first observed in 0.166.0 are
# kept for newer releases, which install and auto-update independently; those
# run with a visible warning instead of failing or silently dropping settings.
VERIFIED_VERSIONS = frozenset({(0, 166, 0), (0, 167, 0)})
MINIMUM_SCHEMA_VERSION = (0, 166, 0)


def schema_supported(version: tuple[int, int, int] | None) -> TypeGuard[tuple[int, int, int]]:
    """True for verified releases and newer 0.x releases using the same settings."""
    return version is not None and version[0] == 0 and version >= MINIMUM_SCHEMA_VERSION


def describe(version: tuple[int, int, int] | None) -> str:
    return "an unknown version" if version is None else ".".join(map(str, version))


def require_schema(version: tuple[int, int, int] | None, feature: str, exception: type[Exception]) -> tuple[int, int, int]:
    """Allow verified/newer releases (newer ones warn); reject older or unknown CLIs."""
    if not schema_supported(version):
        raise exception(
            f"{feature} requires Codex CLI {describe(MINIMUM_SCHEMA_VERSION)} or newer; found {describe(version)}. Update Codex or choose Inherit harness settings."
        )
    if version not in VERIFIED_VERSIONS:
        warnings.warn(
            f"{feature} is verified with Codex CLI {', '.join(sorted(map(describe, VERIFIED_VERSIONS)))}; continuing with the same settings on {describe(version)}.",
            RuntimeWarning,
            stacklevel=3,
        )
    return version


def cross_protocol_overrides(prefix: list[str]) -> list[str]:
    version = detect_version(prefix)
    settings = ['model_reasoning_summary="none"', "features.multi_agent=false", 'web_search="disabled"']
    if schema_supported(version):
        settings.append("agents.enabled=false")
        if version not in VERIFIED_VERSIONS:
            warnings.warn(
                f"Codex CLI {describe(version)} is newer than the verified translated-route releases; retaining the same conservative settings. Subagents remain unverified.",
                RuntimeWarning,
                stacklevel=2,
            )
    elif version != (0, 164, 0):
        warnings.warn(
            "Codex CLI version is not verified for translated routes; retaining conservative compatibility settings. Subagents remain unverified.",
            RuntimeWarning,
            stacklevel=2,
        )
    return settings
