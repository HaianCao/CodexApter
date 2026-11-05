from unittest.mock import patch

import pytest

from codex_adapter.config import Config, Provider
from codex_adapter.launcher import _gateway


@pytest.mark.parametrize("protocol", ["responses", "chat", "messages"])
def test_snapshot_uses_only_explicit_session_models_without_discovery(tmp_path, protocol):
    pytest.importorskip("protoverta")
    provider = Provider("current", "Visible name", "https://example.test/v1", protocol=protocol, execution_mode="gateway")
    provider.agent_models = {"policy": "custom", "default": "vendor/child:free", "effort": "low"}
    other = Provider("other", "Other", "https://other.test/v1", default_model="private-other-model")
    config = Config(providers={"current": provider, "other": other}, path=tmp_path / "config.toml")
    with patch("codex_adapter.launcher.resolve_token", return_value="fake-key"), patch("codex_adapter.catalog.fetch_models") as fetch:
        gateway, _ = _gateway(config, provider, "vendor/main:free")
    fetch.assert_not_called()
    assert [m.id for m in gateway.model_snapshot.models] == ["vendor/main:free", "vendor/child:free"]
    assert all(m.display_name == m.id for m in gateway.model_snapshot.models)
    assert gateway.model_snapshot.matches(provider.base_url, gateway.upstream_protocol)
    assert provider.display_name == "Visible name" and provider.default_model is None
    assert not config.path.exists()


def test_snapshot_deduplicates_selected_child_and_never_modifies_defaults(tmp_path):
    pytest.importorskip("protoverta")
    provider = Provider("p", "P", "https://example.test/v1", execution_mode="gateway", default_model="saved")
    provider.agent_models = {"policy": "custom", "default": "temporary", "effort": "low"}
    config = Config(providers={"p": provider}, path=tmp_path / "config.toml")
    with patch("codex_adapter.launcher.resolve_token", return_value="fake-key"):
        gateway, _ = _gateway(config, provider, "temporary")
    assert [m.id for m in gateway.model_snapshot.models] == ["temporary"]
    assert provider.default_model == "saved"
