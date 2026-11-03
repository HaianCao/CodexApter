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


@pytest.mark.parametrize("policy", ["inherit", "custom"])
def test_auxiliary_model_policies_are_not_enabled_yet(monkeypatch, policy):
    monkeypatch.setattr(subagents, "detect_version", lambda _: (0, 166, 0))
    with pytest.raises(LaunchError, match="same provider/model"):
        subagents.subagent_overrides([], "1", None, "fixture", {"policy": policy})


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
