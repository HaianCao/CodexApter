from unittest.mock import patch

import pytest

from codex_adapter.config import Config, Provider, load, save
from codex_adapter.errors import ConfigError
from codex_adapter.gateway_options import validate_gateway_limits
from codex_adapter.launcher import _gateway


def test_runtime_limits_round_trip_and_reach_gateway(tmp_path):
    provider = Provider("demo", "Demo", "https://example.test/v1", protocol="chat_completions", execution_mode="gateway")
    provider.gateway_limits = {"total_timeout": 321.5, "progress_timeout": 60, "max_stream_items": 20}
    config = Config(providers={"demo": provider}, path=tmp_path / "settings.toml")
    save(config)
    restored = load(config.path)
    assert restored.providers["demo"].gateway_limits == provider.gateway_limits


@pytest.mark.parametrize("value", [{"unknown": 1}, {"total_timeout": float("nan")}, {"timeout": True}, {"max_stream_items": 2.5}, {"progress_timeout": 0}])
def test_invalid_runtime_limits_are_rejected(value):
    with pytest.raises(ConfigError):
        validate_gateway_limits(value, "gateway")


def test_direct_mode_does_not_accept_gateway_limits():
    with pytest.raises(ConfigError):
        validate_gateway_limits({"timeout": 2}, "direct")


def test_runtime_options_reach_optional_gateway():
    pytest.importorskip("protoverta")
    provider = Provider("demo", "Demo", "https://example.test/v1", protocol="chat_completions", execution_mode="gateway")
    provider.gateway_limits = {"total_timeout": 321.5, "max_stream_items": 20}
    with patch("codex_adapter.launcher.resolve_token", return_value="fixture-key"):
        gateway, _ = _gateway(Config(providers={"demo": provider}), provider)
    assert gateway.total_timeout == 321.5 and gateway.max_stream_items == 20
