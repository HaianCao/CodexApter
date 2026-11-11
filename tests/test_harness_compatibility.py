import os
import subprocess
import sys
import warnings

import pytest

from codexapter import harness


def test_legacy_observed_policy_stays_conservative(monkeypatch):
    monkeypatch.setattr(harness, "detect_version", lambda _: (0, 164, 0))
    with warnings.catch_warnings(record=True) as caught:
        assert "features.multi_agent=false" in harness.cross_protocol_overrides(["fixture"])
    assert not caught


def test_native_route_does_not_probe_harness_version(monkeypatch):
    from codexapter import launcher
    from codexapter.config import Config, Provider, SecretReference

    provider = Provider("fixture", "Fixture", "https://example.invalid/v1", auth_secret_ref="main")
    config = Config(providers={"fixture": provider}, secrets={"main": SecretReference("main", env_var="FIXTURE_TOKEN")})
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "fixture.exe")
    monkeypatch.setattr(launcher, "resolve_token", lambda *_: "synthetic")
    monkeypatch.setattr(launcher, "cross_protocol_overrides", lambda *_: pytest.fail("native route must not probe"))
    args, _ = launcher.build_invocation(config, provider, "model", "medium", [])
    assert "model" in args


@pytest.fixture(autouse=True)
def reset_probe_cache():
    harness._probe.cache_clear()
    yield
    harness._probe.cache_clear()


def test_parse_exact_release_and_reject_ambiguous_output():
    assert harness.parse_version(b"codex-cli 0.166.0\r\n") == (0, 166, 0)
    for raw in (
        b"",
        b"1.2",
        b"not-a-cli 1.2.3",
        b"codex-cli 0.166.0-dev",
        b"x" * 257,
        b"codex-cli 0.166.0\ncodex-cli 0.166.0",
        b"\xff",
    ):
        assert harness.parse_version(raw) is None


def test_probe_uses_isolated_profile_without_credentials(tmp_path, monkeypatch):
    script = tmp_path / "fixture.py"
    script.write_text(
        "import os,sys\n"
        "assert sys.argv[1:] == ['--version']\n"
        "assert 'UNUSUAL_CREDENTIAL_NAME' not in os.environ\n"
        "assert 'HTTPS_PROXY' not in os.environ\n"
        "assert 'PYTHONPATH' not in os.environ\n"
        "assert os.getcwd() != os.environ['USERPROFILE']\n"
        "assert os.path.isdir(os.environ['CODEX_HOME'])\n"
        "assert os.path.isdir(os.environ['CLAUDE_CONFIG_DIR'])\n"
        "print('codex-cli 0.166.0')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("UNUSUAL_CREDENTIAL_NAME", "synthetic-private")
    monkeypatch.setenv("HTTPS_PROXY", "synthetic-private")
    before = dict(os.environ)
    assert harness.detect_version([sys.executable, str(script)]) == (0, 166, 0)
    assert os.environ == before


@pytest.mark.parametrize("mode", ["nonzero", "oversized", "malformed"])
def test_invalid_version_is_unknown(tmp_path, mode):
    script = tmp_path / "fixture.py"
    script.write_text(
        {
            "nonzero": "print('codex-cli 0.166.0'); raise SystemExit(1)",
            "oversized": "print('x' * 4096)",
            "malformed": "print('private diagnostic text')",
        }[mode],
        encoding="utf-8",
    )
    assert harness.detect_version([sys.executable, str(script)]) is None


@pytest.mark.parametrize("error", [OSError("synthetic-private"), subprocess.TimeoutExpired("private", 3)])
def test_failed_or_timed_out_probe_returns_unknown(monkeypatch, error):
    def fail(*args, **kwargs):
        assert kwargs["timeout"] == harness.VERSION_PROBE_SECONDS
        raise error

    monkeypatch.setattr(harness.subprocess, "run", fail)
    assert harness.detect_version(["nonexistent-fixture"]) is None


def test_probe_cache_refreshes_when_cli_entry_changes(tmp_path):
    script = tmp_path / "fixture.py"
    script.write_text("print('codex-cli 0.166.0')", encoding="utf-8")
    prefix = [sys.executable, str(script)]
    assert harness.detect_version(prefix) == (0, 166, 0)
    script.write_text("print('a different release output, not verified')", encoding="utf-8")
    assert harness.detect_version(prefix) is None


def test_known_policy_has_no_warning(monkeypatch):
    monkeypatch.setattr(harness, "detect_version", lambda _: (0, 166, 0))
    with warnings.catch_warnings(record=True) as caught:
        settings = harness.cross_protocol_overrides(["fixture"])
    assert not caught
    assert "features.multi_agent=false" in settings
    assert "agents.enabled=false" in settings


@pytest.mark.parametrize("version", [None, (9, 9, 9), (0, 0, 1)])
def test_unknown_version_warns_without_echoing_cli_output_or_enabling_features(monkeypatch, version):
    monkeypatch.setattr(harness, "detect_version", lambda _: version)
    with pytest.warns(RuntimeWarning, match="not verified"):
        settings = harness.cross_protocol_overrides(["fixture"])
    assert "features.multi_agent=false" in settings
    assert not any("=true" in value for value in settings)


@pytest.mark.parametrize("version", [(0, 166, 0), (0, 167, 0)])
def test_verified_versions_keep_schema_settings_silently(monkeypatch, version):
    monkeypatch.setattr(harness, "detect_version", lambda _: version)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert "agents.enabled=false" in harness.cross_protocol_overrides(["fixture"])


def test_newer_version_keeps_schema_settings_with_warning(monkeypatch):
    monkeypatch.setattr(harness, "detect_version", lambda _: (0, 170, 2))
    with pytest.warns(RuntimeWarning, match="0.170.2"):
        assert "agents.enabled=false" in harness.cross_protocol_overrides(["fixture"])


@pytest.mark.parametrize("version", [None, (0, 165, 9), (1, 0, 0)])
def test_require_schema_rejects_unknown_older_or_next_major(version):
    with pytest.raises(ValueError, match="0.166.0 or newer"):
        harness.require_schema(version, "Feature", ValueError)
