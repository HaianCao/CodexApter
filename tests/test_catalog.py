import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from codex_adapter.catalog import cached_models, fetch_models
from codex_adapter.config import Config, Discovery, Provider
from codex_adapter.errors import DiscoveryError


class _Response:
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, size=-1):
        return self.data if size < 0 else self.data[:size]


class CatalogTests(unittest.TestCase):
    def test_default_cache_migrates_from_legacy_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            local = Path(directory) / "local"
            old_root, new_root = local / "codex-ai", local / "codexapter"
            old_root.mkdir(parents=True)
            old_file = old_root / "catalog-cache.json"
            old_file.write_text('{"providers": {"demo": {"models": ["m"], "fetched_at": 0}}}', encoding="utf-8")
            with patch.dict("os.environ", {"LOCALAPPDATA": str(local)}, clear=True):
                models, stale = cached_models(Provider("demo", "Demo", "https://example.test/v1"))
            self.assertEqual(models, ["m"])
            self.assertTrue(stale)
            self.assertTrue((new_root / "catalog-cache.json").exists())
            self.assertFalse(old_file.exists())

    def test_fetch_models_normalizes_ids_and_caches_catalog(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "catalog.json"
            provider = Provider("demo", "Demo", "https://example.test/v1", discovery=Discovery(enabled=True))
            payload = json.dumps({"data": [{"id": " model-b "}, {"id": "model-a"}, {"id": "model-a"}]}).encode()
            with patch("codex_adapter.catalog.urlopen", return_value=_Response(payload)) as open_url:
                models = fetch_models(Config(providers={"demo": provider}), provider, cache=cache)
            self.assertEqual(models, ["model-a", "model-b"])
            self.assertEqual(open_url.call_args.args[0].full_url, "https://example.test/v1/models")
            self.assertEqual(cached_models(provider, cache)[0], models)

    def test_invalid_cache_shape_is_treated_as_empty_and_stale(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "catalog.json"
            cache.write_text("[]", encoding="utf-8")
            provider = Provider("demo", "Demo", "https://example.test/v1")
            self.assertEqual(cached_models(provider, cache), ([], True))

    def test_oversized_catalog_response_is_rejected(self):
        provider = Provider("demo", "Demo", "https://example.test/v1", discovery=Discovery(enabled=True))
        with patch("codex_adapter.catalog.urlopen", return_value=_Response(b"x" * 8_000_001)):
            with self.assertRaises(DiscoveryError):
                fetch_models(Config(providers={"demo": provider}), provider)


if __name__ == "__main__":
    unittest.main()
