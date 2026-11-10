import json
import time

import pytest

from codexapter.catalog import _identity, cached_model_names
from codexapter.config import Config, Provider


@pytest.mark.parametrize("change", ["fresh", "stale", "credential", "identity", "bad_name", "invalid", "oversize"])
def test_cache_enrichment_is_read_only_and_never_adds_model_ids(tmp_path, monkeypatch, change):
    path = tmp_path / "catalog.json"
    monkeypatch.setenv("CODEX_CA_CACHE", str(path))
    provider = Provider("p", "P", "https://fixture.test/v1")
    config = Config(providers={"p": provider}, path=tmp_path / "config.toml")
    entry = {
        "identity": _identity(provider, config, token="synthetic"),
        "fetched_at": time.time(),
        "models": ["a", "b"],
        "model_info": [{"id": "a", "display_name": "Friendly"}, {"id": "b", "display_name": "Other"}],
    }
    if change == "stale":
        entry["fetched_at"] = 0
    if change == "identity":
        entry["identity"]["base_url"] = "https://other.test/v1"
    if change == "bad_name":
        entry["model_info"][0]["display_name"] = "bad\nname"
    raw = "invalid" if change == "invalid" else " " * (8 * 1024 * 1024 + 1) if change == "oversize" else json.dumps({"providers": {"p": entry}})
    path.write_text(raw, encoding="utf-8")
    result = cached_model_names(config, provider, ("a",), token="changed" if change == "credential" else "synthetic")
    assert result == ({"a": "Friendly"} if change == "fresh" else {})
    assert path.read_text(encoding="utf-8") == raw and not config.path.exists()
