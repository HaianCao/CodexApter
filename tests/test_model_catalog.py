import json

import pytest

from codex_adapter.errors import LaunchError
from codex_adapter.model_catalog import catalog_override


def native_model():
    return {
        "slug": "vendor/model",
        "display_name": "Fixture",
        "supported_reasoning_levels": [{"effort": "low", "description": "Low"}],
        "shell_type": "unified_exec",
        "visibility": "list",
        "supported_in_api": True,
        "priority": 0,
        "support_verbosity": False,
        "truncation_policy": {"mode": "tokens", "limit": 4096},
        "experimental_supported_tools": [],
    }


def test_opt_in_catalog_requires_exact_model_without_rewriting_metadata(tmp_path):
    path = tmp_path / "catalog.json"
    raw = json.dumps({"models": [native_model()]})
    path.write_text(raw, encoding="utf-8")
    assert catalog_override(str(path), "vendor/model") == [f"model_catalog_json={json.dumps(str(path.resolve()))}"]
    assert path.read_text(encoding="utf-8") == raw
    with pytest.raises(LaunchError, match="exact entry"):
        catalog_override(str(path), "another/model")


@pytest.mark.parametrize("data", [[], {}, {"models": []}, {"models": [None]}, {"models": [{"slug": "x"}, {"slug": "x"}]}])
def test_invalid_catalog_is_controlled_and_does_not_echo_content(tmp_path, data):
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LaunchError, match="readable Codex catalog"):
        catalog_override(str(path), "x")


def test_absent_catalog_preserves_default_behavior():
    assert catalog_override(None, "anything") == []


@pytest.mark.parametrize("field", list(native_model()))
def test_required_native_field_is_not_filled_from_gpt(tmp_path, field):
    model = native_model()
    model.pop(field)
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"models": [model]}), encoding="utf-8")
    with pytest.raises(LaunchError):
        catalog_override(str(path), "vendor/model")


@pytest.mark.parametrize(
    "change",
    [
        {"priority": True},
        {"supported_in_api": 1},
        {"supported_reasoning_levels": [{"effort": "fake", "description": "x"}]},
        {"default_reasoning_level": "high"},
        {"context_window": -1},
        {"context_window": 20, "max_context_window": 10},
        {"truncation_policy": {"mode": "tokens", "limit": False}},
    ],
)
def test_invalid_native_metadata_rejected_before_harness(tmp_path, change):
    path = tmp_path / "model.json"
    path.write_text(json.dumps({"models": [{**native_model(), **change}]}), encoding="utf-8")
    with pytest.raises(LaunchError, match="complete, valid"):
        catalog_override(str(path), "vendor/model")


def test_catalog_version_gate(tmp_path, monkeypatch):
    from codex_adapter import harness

    path = tmp_path / "model.json"
    path.write_text(json.dumps({"models": [native_model()]}), encoding="utf-8")
    monkeypatch.setattr(harness, "detect_version", lambda _: (0, 167, 0))
    with pytest.raises(LaunchError, match="0.166.0"):
        catalog_override(str(path), "vendor/model", ["codex"])


@pytest.mark.parametrize(
    "raw", ['{"models":[],"models":[]}', '{"models":[{"slug":"vendor/model","extra":NaN}]}', '{"models":[{"slug":"vendor/model","extra":"\\ud800"}]}']
)
def test_catalog_rejects_ambiguous_or_non_unicode_json(tmp_path, raw):
    path = tmp_path / "model.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(LaunchError, match="readable Codex catalog"):
        catalog_override(str(path), "vendor/model")
