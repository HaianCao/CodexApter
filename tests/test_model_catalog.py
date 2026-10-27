import json

import pytest

from codex_adapter.errors import LaunchError
from codex_adapter.model_catalog import catalog_override


def test_opt_in_catalog_requires_exact_model_without_rewriting_metadata(tmp_path):
    path = tmp_path / "catalog.json"
    raw = json.dumps({"models": [{"slug": "vendor/model", "apply_patch_tool_type": "function"}]})
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
