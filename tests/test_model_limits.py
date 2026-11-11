"""Provider context-window override for Codex sessions."""

import pytest

from codexapter import launcher
from codexapter.config import (
    DEFAULT_GATEWAY_MAX_OUTPUT_TOKENS,
    Config,
    Provider,
    SecretReference,
    load,
    save,
    validate_model_limits,
)
from codexapter.errors import ConfigError


def _config(tmp_path, **options):
    provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="main", **options)
    return Config(
        providers={"demo": provider},
        secrets={"main": SecretReference("main", env_var="DEMO_TOKEN")},
        path=tmp_path / "config.toml",
    ), provider


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


def test_reported_context_window_is_used_when_the_provider_sets_none(tmp_path, monkeypatch):
    from codexapter import catalog

    config, provider = _config(tmp_path)
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "codex.exe")
    monkeypatch.setattr(launcher, "resolve_token", lambda *_: "synthetic")
    monkeypatch.setattr(
        catalog,
        "cached_model_limits",
        lambda *_args, **_kwargs: {"context_window_tokens": 1_048_576, "max_output_tokens": None},
    )
    argv, _ = launcher.build_invocation(config, provider, "vendor/model", "medium", [])
    assert "model_context_window=1048576" in argv
    manual_config, manual = _config(tmp_path, model_limits={"context_window": 131072})
    argv, _ = launcher.build_invocation(manual_config, manual, "vendor/model", "medium", [])
    assert "model_context_window=131072" in argv and "model_context_window=1048576" not in argv


def test_direct_catalog_records_reported_limits(tmp_path, monkeypatch):
    import io
    import json

    from codexapter import catalog
    from codexapter.config import Discovery

    config, provider = _config(tmp_path, discovery=Discovery(enabled=True))
    body = {
        "data": [
            {"id": "vendor/model", "context_length": 262144, "top_provider": {"max_completion_tokens": 32768}},
            {"id": "plain/model"},
        ]
    }

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_):
            self.close()

    monkeypatch.setattr(catalog, "_open", lambda *_args, **_kwargs: Response(json.dumps(body).encode()))
    monkeypatch.setattr(catalog, "resolve_token", lambda *_args, **_kwargs: "synthetic")
    assert catalog.fetch_models(config, provider) == ["plain/model", "vendor/model"]
    assert catalog.cached_model_limits(config, provider, "vendor/model") == {
        "context_window_tokens": 262144,
        "max_output_tokens": 32768,
    }
    assert catalog.cached_model_limits(config, provider, "plain/model") == {}
    assert catalog.cached_limits(config, provider) == {
        "vendor/model": {"context_window_tokens": 262144, "max_output_tokens": 32768}
    }
