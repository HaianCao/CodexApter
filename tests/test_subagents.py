import json

import pytest

from codex_adapter import subagents
from codex_adapter.errors import LaunchError


def catalog(tmp_path, **fields):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": [{"slug": "fixture", "supports_search_tool": False, **fields}]}), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("enabled", [None, "0"])
def test_default_does_not_probe_or_load_catalog(monkeypatch, enabled):
    monkeypatch.setattr(subagents, "detect_version", lambda _: pytest.fail("default must not probe"))
    assert subagents.subagent_overrides([], enabled, None, "fixture", {}) == []


@pytest.mark.parametrize("enabled", ["", "true", "yes", "2"])
def test_invalid_opt_in_rejected(enabled):
    with pytest.raises(LaunchError, match="0 or 1"):
        subagents.subagent_overrides([], enabled, None, "fixture", {})


@pytest.mark.parametrize("version", [None, (0, 164, 0), (0, 167, 0)])
def test_unknown_or_unverified_version_cannot_enable(monkeypatch, version):
    monkeypatch.setattr(subagents, "detect_version", lambda _: version)
    with pytest.raises(LaunchError, match="0.166.0"):
        subagents.subagent_overrides([], "1", None, "fixture", {"policy": "selected"})


def test_inherited_model_policy_cannot_enable_subagents(monkeypatch):
    monkeypatch.setattr(subagents, "detect_version", lambda _: (0, 166, 0))
    with pytest.raises(LaunchError, match="selected or custom"):
        subagents.subagent_overrides([], "1", None, "fixture", {"policy": "inherit"})


@pytest.mark.parametrize("fields", [{"supports_search_tool": True}, {"supports_search_tool": None}, {"tool_mode": "code_mode_only"}])
def test_deferred_or_code_only_tools_remain_outside_verified_subset(tmp_path, monkeypatch, fields):
    monkeypatch.setattr(subagents, "detect_version", lambda _: (0, 166, 0))
    with pytest.raises(LaunchError, match="Deferred tool search"):
        subagents.subagent_overrides([], "1", catalog(tmp_path, **fields), "fixture", {"policy": "selected"})


def test_exact_catalog_and_selected_model_enable_both_observed_flags(tmp_path, monkeypatch):
    monkeypatch.setattr(subagents, "detect_version", lambda _: (0, 166, 0))
    assert subagents.subagent_overrides([], "1", catalog(tmp_path), "fixture", {"policy": "selected"}) == ["features.multi_agent=true", "agents.enabled=true", "agents.max_concurrent_threads_per_session=1"]


def test_missing_catalog_or_unknown_model_fails_before_spawn(tmp_path, monkeypatch):
    monkeypatch.setattr(subagents, "detect_version", lambda _: (0, 166, 0))
    with pytest.raises(LaunchError, match="exact model entry"):
        subagents.subagent_overrides([], "1", None, "fixture", {"policy": "selected"})
    with pytest.raises(LaunchError, match="no exact entry"):
        subagents.subagent_overrides([], "1", catalog(tmp_path), "other-model", {"policy": "selected"})


@pytest.mark.parametrize("fault", [None, "missing_child", "child_search", "child_code_mode", "wrong_effort"])
def test_custom_child_requires_its_own_exact_catalog_profile_and_effort(tmp_path, monkeypatch, fault):
    monkeypatch.setattr(subagents, "detect_version", lambda _: (0, 166, 0))
    main = {"slug": "main", "supports_search_tool": False}
    child = {"slug": "worker", "supports_search_tool": fault == "child_search", "supported_reasoning_levels": [{"effort": "high" if fault == "wrong_effort" else "low"}]}
    if fault == "child_code_mode":
        child["tool_mode"] = "code_mode_only"
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": [main] if fault == "missing_child" else [main, child]}), encoding="utf-8")
    options = {"policy": "custom", "default": "worker", "effort": "low"}
    if fault:
        with pytest.raises(LaunchError):
            subagents.subagent_overrides([], "1", str(path), "main", options)
    else:
        flags = subagents.subagent_overrides([], "1", str(path), "main", options)
        assert "agents.max_concurrent_threads_per_session=1" in flags
        assert not any("default_subagent_model" in flag for flag in flags)


@pytest.mark.parametrize("main,expected", [("main", True), ("worker", False)])
def test_gateway_blocks_unsafe_reopen_only_when_configured_child_model_differs(main, expected):
    pytest.importorskip("protoverta")
    from codex_adapter.config import Config, Provider
    from codex_adapter.launcher import _gateway

    provider = Provider("fixture", "Fixture", "https://example.invalid/v1", protocol="chat_completions", execution_mode="gateway", agent_models={"policy": "custom", "default": "worker", "effort": "low"})
    from unittest.mock import patch

    with patch("codex_adapter.launcher.resolve_token", return_value="synthetic"):
        config, _ = _gateway(Config(), provider, main)
    assert config.codex_model_safe_resume is expected
