import json

import pytest

from codexapter.config import Config, Provider, load, save, validate_base_url
from codexapter.errors import ConfigError
from codexapter.launcher import build_invocation


@pytest.mark.parametrize(
    "suffix", ["responses", "messages", "chat/completions", "responses/", "messages/", "chat/completions/"]
)
def test_endpoint_suffix_is_rejected_before_load_save_or_launch(tmp_path, monkeypatch, suffix):
    value = "https://example.test/api/v1/" + suffix
    with pytest.raises(ConfigError, match="endpoint suffix"):
        validate_base_url(value)
    path = tmp_path / "config.toml"
    original = f"schema_version = 1\n[providers.fixture]\nbase_url = {json.dumps(value)}\n"
    path.write_text(original)
    with pytest.raises(ConfigError, match="endpoint suffix"):
        load(path)
    provider = Provider("fixture", "Fixture", value)
    with pytest.raises(ConfigError, match="endpoint suffix"):
        save(Config(providers={"fixture": provider}, path=path))
    assert path.read_text() == original

    def forbidden(*args, **kwargs):
        pytest.fail("invalid prefix must be rejected before reading credentials")

    monkeypatch.setattr("codexapter.launcher.resolve_token", forbidden)
    with pytest.raises(ConfigError, match="endpoint suffix"):
        build_invocation(Config(), provider, "m", "medium", [])


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/v1",
        "https://example.test/api/v1",
        "https://example.test/custom/prefix",
        "http://127.0.0.1:12345/v1",
    ],
)
def test_exact_api_prefix_is_retained(value):
    assert validate_base_url(value + "/") == value


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test/with space",
        "https://exa mple.test/v1",
        " https://example.test/v1",
        "https://example.test/v1\n",
        "\0https://example.test/v1",
        "https://example.test/v1\0",
        "https://example.test/\tpath",
        "https://example.test/\rpath",
        "https://example.test/\x1fpath",
        "https://example.test/\x7fpath",
        "https://example.test/\u00a0path",
        "https://example.test:0/v1",
        "http://127.0.0.1:0/v1",
        "https://example.test:65536/v1",
        "https://example.test:-1/v1",
    ],
)
def test_invalid_url_rejected_before_load_save_or_launch(tmp_path, monkeypatch, value):
    with pytest.raises(ConfigError):
        validate_base_url(value)
    path = tmp_path / "config.toml"
    original = f"schema_version = 1\n[providers.fixture]\nbase_url = {json.dumps(value)}\n"
    path.write_text(original)
    with pytest.raises(ConfigError):
        load(path)
    provider = Provider("fixture", "Fixture", value)
    with pytest.raises(ConfigError):
        save(Config(providers={"fixture": provider}, path=path))
    assert path.read_text() == original
    monkeypatch.setattr(
        "codexapter.launcher.resolve_token", lambda *_: pytest.fail("must reject URL before credential access")
    )
    with pytest.raises(ConfigError):
        build_invocation(Config(), provider, "m", "medium", [])


@pytest.mark.parametrize(
    "value",
    [
        "https://example.test:1/v1",
        "https://example.test:65535/api/v1",
        "https://example.test/encoded%20path/v1",
        "http://[::1]:12345/v1",
    ],
)
def test_valid_ports_encoded_paths_and_ipv6_are_preserved(value):
    assert validate_base_url(value) == value
