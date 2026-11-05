import io
from http.client import IncompleteRead

import pytest

from codex_adapter import catalog
from codex_adapter.config import Config, Provider
from codex_adapter.errors import DiscoveryError

INVALID = [b'{"x":' + b"1" * 5000 + b"}", b'{"x":' + b"[" * 10000 + b"0" + b"]" * 10000 + b"}", b'{"x":"\\ud800"}', b'{"x":NaN}', b'{"x":1e309}', b"{", b"\xff"]


def config_and_provider(tmp_path, monkeypatch):
    config = Config(path=tmp_path / "settings.toml")
    provider = Provider("fixture", "Fixture", "https://example.invalid/v1")
    provider.discovery.enabled = True
    monkeypatch.setattr(catalog, "resolve_token", lambda *args, **kwargs: None)
    return config, provider


@pytest.mark.parametrize("raw", INVALID)
def test_bad_cache_falls_back_without_parser_exception(tmp_path, monkeypatch, raw):
    config, provider = config_and_provider(tmp_path, monkeypatch)
    path = tmp_path / "cache.json"
    path.write_bytes(raw)
    assert catalog.cached_models(provider, path, config=config) == ([], True)


@pytest.mark.parametrize("raw", INVALID)
def test_bad_provider_json_is_typed_and_valid_retry_recovers(tmp_path, monkeypatch, raw):
    config, provider = config_and_provider(tmp_path, monkeypatch)
    payloads = iter([raw, b'{"data":[{"id":"fixture-model"}]}'])

    def upstream(*args, **kwargs):
        response = io.BytesIO(next(payloads))
        response.headers = {}
        return response

    monkeypatch.setattr(catalog, "_open", upstream)
    fetch = lambda: catalog.fetch_models(config, provider, cache=tmp_path / "cache.json")
    with pytest.raises(DiscoveryError):
        fetch()
    assert fetch() == ["fixture-model"]


def test_truncated_provider_response_has_typed_error_and_closes(tmp_path, monkeypatch):
    config, provider = config_and_provider(tmp_path, monkeypatch)

    class Broken(io.BytesIO):
        def __init__(self):
            super().__init__()
            self.headers = {}

        def read(self, size=-1):
            raise IncompleteRead(b"synthetic-partial", 1)

    response = Broken()
    monkeypatch.setattr(catalog, "_open", lambda *args, **kwargs: response)
    with pytest.raises(DiscoveryError) as caught:
        catalog.fetch_models(config, provider, cache=tmp_path / "cache.json")
    assert "synthetic-partial" not in str(caught.value)
    assert response.closed
