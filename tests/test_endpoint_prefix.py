import json

import pytest

from codex_adapter.config import Config, Provider, load, save, validate_base_url
from codex_adapter.errors import ConfigError
from codex_adapter.launcher import build_invocation


@pytest.mark.parametrize("suffix", ["responses", "messages", "chat/completions", "responses/", "messages/", "chat/completions/"])
def test_endpoint_suffix_is_rejected_before_load_save_or_launch(tmp_path, monkeypatch, suffix):
    value = "https://example.test/api/v1/" + suffix
    with pytest.raises(ConfigError, match="endpoint suffix"):
        validate_base_url(value)
    path = tmp_path / "config.toml"
    original = f'schema_version = 1\n[providers.fixture]\nbase_url = {json.dumps(value)}\n'
    path.write_text(original)
    with pytest.raises(ConfigError, match="endpoint suffix"):
        load(path)
    provider = Provider("fixture", "Fixture", value)
    with pytest.raises(ConfigError, match="endpoint suffix"):
        save(Config(providers={"fixture": provider}, path=path))
    assert path.read_text() == original

    def forbidden(*args, **kwargs):
        pytest.fail("invalid prefix must be rejected before reading credentials")

    monkeypatch.setattr("codex_adapter.launcher.resolve_token", forbidden)
    with pytest.raises(ConfigError, match="endpoint suffix"):
        build_invocation(Config(), provider, "m", "medium", [])


@pytest.mark.parametrize("value", ["https://example.test/v1", "https://example.test/api/v1", "https://example.test/custom/prefix", "http://127.0.0.1:12345/v1"])
def test_exact_api_prefix_is_retained(value):
    assert validate_base_url(value + "/") == value
