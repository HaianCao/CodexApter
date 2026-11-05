import json
from unittest.mock import patch

import pytest

from codex_adapter.catalog import cached_models, fetch_models
from codex_adapter.config import Config, Discovery, Provider
from codex_adapter.errors import ConfigError
from codex_adapter.gateway_options import runtime_options, validate_gateway_runtime


def test_gateway_catalog_mapping_cache_scope_and_no_certification(tmp_path):
    pytest.importorskip("protoverta.discovery")
    from protoverta.discovery import ModelInfo

    provider = Provider("demo", "Demo", "https://example.test/api/v1", protocol="chat", execution_mode="gateway", discovery=Discovery(enabled=True))
    config = Config(providers={"demo": provider}, path=tmp_path / "config.toml")
    cache = tmp_path / "cache.json"
    with patch("codex_adapter.catalog.resolve_token", return_value="fixture-one"), patch("protoverta.discovery.fetch_catalog", return_value=(ModelInfo("m", "Model", context_window_tokens=1000),)) as fetch:
        assert fetch_models(config, provider, cache=cache) == ["m"]
        assert fetch.call_args.args[0] == provider.base_url
        assert cached_models(provider, cache, config=config) == (["m"], False)
    with patch("codex_adapter.catalog.resolve_token", return_value="fixture-two"):
        assert cached_models(provider, cache, config=config) == ([], True)
    body = cache.read_text()
    assert "fixture-one" not in body and "fixture-two" not in body
    assert json.loads(body)["providers"]["demo"]["model_info"][0]["capability_status"] == "unknown"


def test_new_runtime_policies_round_trip_without_importing_catalog_settings_into_gateway(tmp_path):
    values = {"catalog_policy_file": "catalog.json", "context_preflight": "estimate", "require_verified_routes": True}
    assert validate_gateway_runtime(values, "gateway") == values
    assert runtime_options(values, "gateway", tmp_path / "config.toml") == {"context_preflight": "estimate", "require_verified_routes": True}
    for bad in ({"context_preflight": "exact"}, {"require_verified_routes": 1}):
        with pytest.raises(ConfigError):
            validate_gateway_runtime(bad, "gateway")
