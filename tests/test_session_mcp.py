import json
import sys

import pytest

from codexapter.mcp import codex_overrides, load_servers


def test_mcp_uses_only_explicit_env_names_not_values(tmp_path):
    path = tmp_path / "servers.json"
    path.write_text(
        json.dumps({"mcpServers": {"search": {"command": sys.executable, "args": ["-m", "example"], "env_vars": ["SEARCH_TOKEN"]}}}), encoding="utf-8"
    )
    settings = codex_overrides(load_servers(str(path)))
    assert 'mcp_servers.search.env_vars=["SEARCH_TOKEN"]' in settings
    assert "mcp_servers.search.required=true" in settings
    assert load_servers(None) == {}


@pytest.mark.parametrize(
    "server",
    [
        {"command": "relative.exe"},
        {"url": "https://example.invalid"},
        {"command": sys.executable, "env": {"TOKEN": "do-not-echo"}},
        {"command": sys.executable, "args": [42]},
        {"command": sys.executable, "env_vars": ["TOKEN=secret"]},
    ],
)
def test_invalid_mcp_has_controlled_error_without_contents(tmp_path, server):
    path = tmp_path / "servers.json"
    path.write_text(json.dumps({"mcpServers": {"search": server}}), encoding="utf-8")
    with pytest.raises(ValueError) as failure:
        load_servers(str(path))
    assert "do-not-echo" not in str(failure.value)
    assert "secret" not in str(failure.value)
