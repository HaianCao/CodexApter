import json

import pytest

from codexapter import catalog
from codexapter.config import Config, Provider


@pytest.mark.parametrize(
    "fetched_at,stale",
    [
        (1_700_000_000, False),
        (1_699_999_999.5, False),
        (1_700_000_000 - 90, False),
        (1_700_000_000 - 91, True),
        ("nan", True),
        ("inf", True),
        ("-inf", True),
        ("1700000000", True),
        (True, True),
        (False, True),
        (None, True),
        ([], True),
        ({}, True),
        (-1, True),
        (-0.5, True),
        (1_700_000_001, True),
        (10**500, True),
    ],
    ids=[
        "fresh-int",
        "fresh-float",
        "ttl-boundary",
        "expired",
        "nan-string",
        "inf-string",
        "negative-inf-string",
        "numeric-string",
        "true",
        "false",
        "null",
        "array",
        "object",
        "negative-int",
        "negative-float",
        "future",
        "huge-int",
    ],
)
def test_cache_timestamp_preserves_models_but_marks_invalid_age_stale(tmp_path, monkeypatch, fetched_at, stale):
    config = Config(path=tmp_path / "config.toml")
    provider = Provider("fixture", "Fixture", "https://example.invalid/v1")
    provider.discovery.cache_ttl_seconds = 90
    monkeypatch.setattr(catalog, "resolve_token", lambda *args, **kwargs: "")
    monkeypatch.setattr(catalog.time, "time", lambda: 1_700_000_000)
    path = tmp_path / "cache.json"
    raw = json.dumps(
        {
            "providers": {
                provider.id: {
                    "identity": catalog._identity(provider, config),
                    "models": ["m"],
                    "fetched_at": fetched_at,
                }
            }
        }
    ).encode()
    path.write_bytes(raw)
    assert catalog.cached_models(provider, path, config=config) == (["m"], stale)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("fetched_at", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_json_cache_is_rejected(tmp_path, monkeypatch, fetched_at):
    config = Config(path=tmp_path / "config.toml")
    provider = Provider("fixture", "Fixture", "https://example.invalid/v1")
    monkeypatch.setattr(catalog, "resolve_token", lambda *args, **kwargs: "")
    path = tmp_path / "cache.json"
    path.write_text(
        json.dumps(
            {
                "providers": {
                    provider.id: {
                        "identity": catalog._identity(provider, config),
                        "models": ["m"],
                        "fetched_at": fetched_at,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    assert catalog.cached_models(provider, path, config=config) == ([], True)
