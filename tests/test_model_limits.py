"""Provider context-window override for Codex sessions."""

import pytest

from codex_adapter import launcher
from codex_adapter.config import DEFAULT_GATEWAY_MAX_OUTPUT_TOKENS, Config, Provider, SecretReference, load, save, validate_model_limits
from codex_adapter.errors import ConfigError


def _config(tmp_path, **options):
    provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="main", **options)
    return Config(providers={"demo": provider}, secrets={"main": SecretReference("main", env_var="DEMO_TOKEN")}, path=tmp_path / "config.toml"), provider


def test_context_window_round_trips_and_becomes_a_codex_override(tmp_path, monkeypatch):
    config, provider = _config(tmp_path, model_limits={"context_window": 131072})
    save(config)
    assert load(config.path).providers["demo"].model_limits == {"context_window": 131072}
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "codex.exe")
    monkeypatch.setattr(launcher, "resolve_token", lambda *_: "synthetic")
    argv, _ = launcher.build_invocation(config, provider, "vendor/model", "medium", [])
    assert "model_context_window=131072" in argv


def test_no_override_without_a_configured_window(tmp_path, monkeypatch):
    config, provider = _config(tmp_path)
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "codex.exe")
    monkeypatch.setattr(launcher, "resolve_token", lambda *_: "synthetic")
    argv, _ = launcher.build_invocation(config, provider, "vendor/model", "medium", [])
    assert not any(arg.startswith("model_context_window") for arg in argv)
    assert provider.gateway_max_output_tokens == DEFAULT_GATEWAY_MAX_OUTPUT_TOKENS == 16384


@pytest.mark.parametrize("value", [{"context_window": 0}, {"context_window": "1"}, {"max_output_tokens": 1}, []])
def test_invalid_model_limits_are_rejected(value):
    with pytest.raises(ConfigError):
        validate_model_limits(value)
