import json
import queue
import re
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

from codex_adapter.config import Config, Defaults, ModelAlias, Provider, SecretReference
from codex_adapter.web import _HTML, _default_secret_names, _remove_provider_config, serve


class WebConfigTests(unittest.TestCase):
    def test_page_uses_full_artwork_for_header_and_icon_variants_for_favicon(self):
        self.assertIn('id="brandLogo" src="/assets/CodexApter_black.png"', _HTML)
        self.assertIn('id="favicon" rel="icon" type="image/png" href="/assets/CodexApter_black_icon.png"', _HTML)
        self.assertIn("const icon = `/assets/CodexApter_${light ? 'white' : 'black'}_icon.png`", _HTML)

    def test_default_secret_names_follow_provider_id_convention(self):
        self.assertEqual(_default_secret_names("agent_router"), ("agent_router-main", "AGENT_ROUTER_TOKEN"))
        self.assertEqual(_default_secret_names("agent-router"), ("agent-router-main", "AGENT_ROUTER_TOKEN"))

    def test_delete_provider_removes_unused_secret_and_aliases(self):
        with tempfile.TemporaryDirectory() as directory:
            env_path = Path(directory) / ".env"
            env_path.write_text('DEMO_KEY="secret"\n', encoding="utf-8")
            config = Config(
                defaults=Defaults(provider="demo", model="fast"),
                providers={"demo": Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-ref")},
                secrets={"demo-ref": SecretReference("demo-ref", env_var="DEMO_KEY")},
                models={"fast": ModelAlias("fast", "demo", "model-id")},
                env_file=env_path,
            )
            env_var = _remove_provider_config(config, "demo")
            self.assertEqual(env_var, "DEMO_KEY")
            self.assertFalse(config.providers)
            self.assertFalse(config.secrets)
            self.assertFalse(config.models)
            self.assertIsNone(config.defaults.provider)

    def test_delete_preserves_secret_shared_by_another_provider(self):
        shared = SecretReference("shared", env_var="SHARED_KEY")
        config = Config(
            providers={
                "one": Provider("one", "One", "https://one.test/v1", auth_secret_ref="shared"),
                "two": Provider("two", "Two", "https://two.test/v1", auth_secret_ref="shared"),
            },
            secrets={"shared": shared},
        )
        self.assertIsNone(_remove_provider_config(config, "one"))
        self.assertIn("shared", config.secrets)
        self.assertIn("two", config.providers)

    def test_run_hands_off_to_codex_in_the_setup_foreground_process(self):
        with tempfile.TemporaryDirectory() as directory:
            provider = Provider("demo", "Demo", "https://example.test/v1", auth_secret_ref="demo-key")
            config = Config(
                providers={"demo": provider},
                secrets={"demo-key": SecretReference("demo-key", env_var="DEMO_TOKEN")},
                path=Path(directory) / "config.toml",
                env_file=Path(directory) / ".env",
            )
            opened = queue.Queue()
            launch_threads = []
            result = {}

            def fake_launch(*_args):
                launch_threads.append(threading.get_ident())
                return 0

            with patch("codex_adapter.web.webbrowser.open", side_effect=opened.put), patch(
                "codex_adapter.web.build_invocation", return_value=(["codex"], {})
            ), patch("codex_adapter.web.launch", side_effect=fake_launch):
                setup_thread = threading.Thread(target=lambda: result.setdefault("code", serve(config)))
                setup_thread.start()
                url = opened.get(timeout=3)
                html = urlopen(url, timeout=3).read().decode("utf-8")
                token = re.search(r"const SESSION_TOKEN = '([^']+)'", html).group(1)
                request = Request(
                    url + "api/run",
                    data=json.dumps({"provider": "demo", "model": "model-id", "reasoning": "low"}).encode(),
                    headers={"Content-Type": "application/json", "X-Codex-Apter-Token": token},
                    method="POST",
                )
                with urlopen(request, timeout=3) as response:
                    self.assertEqual(json.loads(response.read()), {"started": True})
                setup_thread.join(timeout=3)

            self.assertFalse(setup_thread.is_alive())
            self.assertEqual(result["code"], 0)
            self.assertEqual(launch_threads, [setup_thread.ident])


if __name__ == "__main__":
    unittest.main()
