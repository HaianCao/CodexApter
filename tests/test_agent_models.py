import os
import tomllib

import pytest

from codex_adapter import agent_models, launcher
from codex_adapter.config import Config, Provider, SecretReference, load, save
from codex_adapter.errors import ConfigError, LaunchError


def make_config(tmp_path, options):
    provider = Provider("fixture", "Fixture", "https://example.invalid/v1", agent_models=options, auth_secret_ref="main")
    config = Config(providers={"fixture": provider}, secrets={"main": SecretReference("main", env_var="FIXTURE_TOKEN")}, path=tmp_path / "config.toml")
    return config, provider


@pytest.mark.parametrize("options", [{}, {"policy": "inherit"}, {"policy": "selected"}, {"policy": "custom", "default": "provider/worker", "effort": "low"}])
def test_config_roundtrip_and_public_snapshot(tmp_path, monkeypatch, options):
    from codex_adapter import web
    
    config, _ = make_config(tmp_path, options)
    save(config)
    restored = load(config.path)
    assert restored.providers["fixture"].agent_models == options
    assert web.public_config(restored)["providers"][0]["agent_models"] == options
    if not options:
        assert "agent_models" not in config.path.read_text(encoding="utf-8")


@pytest.mark.parametrize("value", [None, [], True, "selected", {"policy": []}, {"policy": "bad"},
    {"policy": "inherit", "default": "m"}, {"policy": "selected", "default": "m"},
    {"policy": "custom"}, {"policy": "custom", "default": ""}, {"policy": "custom", "default": "bad model"},
    {"policy": "custom", "default": "a\x00b"}, {"policy": "custom", "default": chr(0xD800)},
    {"policy": "custom", "default": "x" * 513}, {"unknown": "x"},
    {"policy": "custom", "default": "m", "effort": "xhigh"}, {"policy": "custom", "default": "m", "effort": "inherit"}, {"policy": "selected", "haiku": "m"}])
def test_invalid_policy_never_overwrites_config(tmp_path, value):
    config, provider = make_config(tmp_path, {})
    save(config)
    before = config.path.read_bytes()
    provider.agent_models = value
    with pytest.raises(ConfigError):
        save(config)
    assert config.path.read_bytes() == before


def test_legacy_missing_policy_is_preserved(tmp_path):
    config, _ = make_config(tmp_path, {})
    save(config)
    loaded = load(config.path)
    assert loaded.providers["fixture"].agent_models == {}
    monkey_options = agent_models.agent_overrides([], {}, 'selected', 'high')
    assert monkey_options == []


def test_inherit_preserves_external_agent_choices_without_version_probe(tmp_path, monkeypatch):
    config, provider = make_config(tmp_path, {"policy": "inherit"})
    monkeypatch.setattr(agent_models, "detect_version", lambda _: pytest.fail("inherit must not probe"))
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "fixture.exe")
    monkeypatch.setattr(launcher, "resolve_token", lambda *_: "synthetic")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "existing-fast")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "1")
    args, env = launcher.build_invocation(config, provider, "provider/main", "high", [])
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "existing-fast"
    assert env["CLAUDE_CODE_SUBAGENT_MODEL_FORCE"] == "1"
    assert not any("default_subagent" in item for item in args)


def test_exact_model_quotes_roundtrip_without_injection(tmp_path):
    model = 'provider/<tag>"quoted"'
    options = {"policy": "custom", "default": model, "effort": "low"}
    config, _ = make_config(tmp_path, options)
    save(config)
    assert load(config.path).providers["fixture"].agent_models["default"] == model


def test_non_bmp_model_is_valid_toml_in_runtime_overrides(monkeypatch):
    monkeypatch.setattr(agent_models, "detect_version", lambda _: (0, 166, 0))
    model = "provider/模型😀"
    values = agent_models.agent_overrides(["fixture"], {"policy": "selected"}, model, "medium")
    assert tomllib.loads("\n".join(values))["agents"]["default_subagent_model"] == model


@pytest.mark.parametrize("gateway", [False, True])
def test_selected_policy_is_child_only_and_keeps_auth_and_main_effort(tmp_path, monkeypatch, gateway):
    config, provider = make_config(tmp_path, {"policy": "selected"})
    monkeypatch.setattr(agent_models, "detect_version", lambda _: (0, 166, 0))
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "fixture.exe")
    monkeypatch.setattr(launcher, "resolve_token", lambda *_: "synthetic-provider")
    monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "old-model")
    monkeypatch.setenv("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "1")
    monkeypatch.setenv("UNRELATED_SETTING", "preserve-me")
    if gateway:
        provider.execution_mode = "gateway"
        provider.protocol = "chat"
        monkeypatch.setattr(launcher, "cross_protocol_overrides", lambda _: [])
    before = dict(os.environ)
    args, env = launcher.build_invocation(config, provider, "provider/selected", "high", [],
        **({"gateway_url": "http://127.0.0.1:4567", "gateway_token": "synthetic-local"} if gateway else {}))
    assert os.environ == before
    assert env["UNRELATED_SETTING"] == "preserve-me"
    assert "provider/selected" in args
    settings = tomllib.loads("\n".join(args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "-c"))
    assert settings["agents"]["default_subagent_model"] == "provider/selected"
    assert settings["agents"]["default_subagent_reasoning_effort"] == "high"
    assert settings["model_reasoning_effort"] == "high"
    assert "synthetic-provider" not in " ".join(args)


def test_explicit_policy_on_unknown_version_fails_before_launch(tmp_path, monkeypatch):
    config, provider = make_config(tmp_path, {"policy": "selected"})
    monkeypatch.setattr(agent_models, "detect_version", lambda _: None)
    monkeypatch.setattr(launcher.shutil, "which", lambda _: "fixture.exe")
    monkeypatch.setattr(launcher, "resolve_token", lambda *_: "synthetic-provider")
    with pytest.raises(LaunchError, match="Inherit"):
        launcher.build_invocation(config, provider, "provider/selected", "high", [])


def test_custom_policy_has_explicit_precedence_and_effort(monkeypatch):
    monkeypatch.setattr(agent_models, "detect_version", lambda _: (0, 166, 0))
    settings = agent_models.agent_overrides(["fixture"], {"policy": "custom", "default": "provider/worker", "effort": "low"}, "provider/main", "high")
    assert 'agents.default_subagent_model="provider/worker"' in settings
    assert 'agents.default_subagent_reasoning_effort="low"' in settings
    assert not any(value.startswith("model_reasoning_effort=") for value in settings)
