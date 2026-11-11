import json
import os
import queue
import re
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from codexapter.config import Config, Provider, SecretReference, load, save
from codexapter.errors import ConfigError
from codexapter.web import _new_secret_names, persist_change, serve


@pytest.mark.parametrize("env", ["TEST_A_TOKEN", "test_a_token"])
def test_new_provider_cannot_implicitly_share_a_normalized_key_name(env):
    config = Config(secrets={"test-a-main": SecretReference("test-a-main", env_var=env)})
    with pytest.raises(ConfigError, match="conflicts"):
        _new_secret_names(config, "test_a")


def test_new_provider_cannot_overwrite_an_unattached_secret_reference():
    config = Config(secrets={"demo-main": SecretReference("demo-main", env_var="CUSTOM_TOKEN")})
    with pytest.raises(ConfigError, match="conflicts"):
        _new_secret_names(config, "demo")


def test_duplicate_secret_environment_names_are_rejected_before_key_write(tmp_path):
    config = Config(path=tmp_path / "config.toml", env_file=tmp_path / ".env")
    config.path.write_bytes(b"prior config")
    config.env_file.write_bytes(b"prior secrets")
    updated = Config(
        path=config.path,
        secrets={
            "one": SecretReference("one", env_var="DEMO_TOKEN"),
            "two": SecretReference("two", env_var="demo_token"),
        },
    )
    with patch("codexapter.web.store_token") as store, pytest.raises(ConfigError, match="same environment"):
        persist_change(config, updated, "DEMO_TOKEN", "fixture")
    store.assert_not_called()
    assert config.path.read_bytes() == b"prior config"
    assert config.env_file.read_bytes() == b"prior secrets"
    with pytest.raises(ConfigError, match="same environment"):
        save(updated)
    config.path.write_text(
        '[secrets.one]\nenv_var="DEMO_TOKEN"\n[secrets.two]\nenv_var="demo_token"\n', encoding="utf-8"
    )
    with pytest.raises(ConfigError, match="same environment"):
        load(config.path)


def test_explicit_shared_reference_remains_supported(tmp_path):
    config = Config(
        path=tmp_path / "config.toml",
        providers={
            name: Provider(name, name, "https://example.test/v1", auth_secret_ref="shared") for name in ("a-b", "a_b")
        },
        secrets={"shared": SecretReference("shared", env_var="SHARED_TOKEN")},
    )
    save(config)
    assert {provider.auth_secret_ref for provider in load(config.path).providers.values()} == {"shared"}


def test_setup_api_collision_does_not_change_first_provider_key(tmp_path):
    opened = queue.Queue()
    listeners = []
    config = Config(path=tmp_path / "config.toml", env_file=tmp_path / ".env")

    def server_factory(*args, **kwargs):
        server = ThreadingHTTPServer(*args, **kwargs)
        listeners.append(server)
        return server

    with (
        patch.dict(os.environ, {"CODEX_CA_ENV_FILE": str(config.env_file), "TEST_A_TOKEN": "fixture-process-before"}),
        patch("codexapter.web.ThreadingHTTPServer", side_effect=server_factory),
        patch("codexapter.web.webbrowser.open", side_effect=opened.put),
    ):
        worker = threading.Thread(target=serve, args=(config,), daemon=True)
        worker.start()
        try:
            url = opened.get(timeout=3)
            with urlopen(url, timeout=3) as response:
                token = re.search(r"const SESSION_TOKEN = '([^']+)'", response.read().decode())[1]
            origin = url.split("?", 1)[0]

            def post(provider_id, key):
                body = {
                    "id": provider_id,
                    "display_name": provider_id,
                    "base_url": "https://example.test/v1",
                    "api_key": key,
                    "discovery": False,
                }
                return urlopen(
                    Request(
                        origin + "api/provider", data=json.dumps(body).encode(), headers={"X-Codex-Apter-Token": token}
                    ),
                    timeout=3,
                )

            with post("test-a", "fixture-original"):
                pass
            before = config.env_file.read_bytes()
            with pytest.raises(HTTPError) as collision:
                post("test_a", "fixture-overwrite")
            assert collision.value.code == 400
            collision.value.close()
            assert config.env_file.read_bytes() == before
            assert os.environ["TEST_A_TOKEN"] == "fixture-original"
            assert set(config.providers) == {"test-a"}
        finally:
            for server in listeners:
                server.shutdown()
            worker.join(timeout=3)
            assert not worker.is_alive()
