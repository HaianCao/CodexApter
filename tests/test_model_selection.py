import pytest

from codexapter.config import Config, Provider
from codexapter.model_selection import model_status


def test_setup_status_uses_exact_route_and_keeps_missing_evidence_unknown(tmp_path, monkeypatch):
    pytest.importorskip("protoverta")
    from protoverta.capabilities import (
        CapabilityRecord,
        CapabilityRegistry,
        RouteCapabilityRecord,
        route_policies,
    )
    from protoverta.server import GatewayConfig

    provider = Provider(
        "p",
        "P",
        "https://fixture.test/v1",
        protocol="chat",
        execution_mode="gateway",
        capabilities_file="evidence.json",
    )
    config = Config(providers={"p": provider}, path=tmp_path / "config.toml")
    gateway = GatewayConfig(
        "chat", provider.base_url, "key", "local", client_profile="codex", namespace_tools="flatten"
    )
    good = CapabilityRecord(
        provider.base_url,
        "chat",
        "ok",
        {"text": True, "streaming": True},
        "2026-11-04",
        "fixture",
        context_window_tokens=16384,
    )
    bad = CapabilityRecord(provider.base_url, "chat", "bad", {"streaming": False}, "2026-11-04", "fixture")
    route = RouteCapabilityRecord(
        provider.base_url,
        "chat",
        "ok",
        "responses",
        True,
        "codex",
        route_policies(gateway),
        good.features,
        "2026-11-04",
        "fixture",
        "fixture",
    )
    registry = CapabilityRegistry((good, bad), (route,))
    monkeypatch.setattr(CapabilityRegistry, "load", lambda path: registry)
    ok_status = model_status(config, provider, "ok")
    assert ok_status["status"] == "verified"
    assert ok_status["count_category"] == "unavailable"
    assert ok_status["context_window_tokens"] == 16384
    assert "Token count: unavailable; context window: 16384." in ok_status["message"]
    assert model_status(config, provider, "bad")["status"] == "unsupported"
    assert model_status(config, provider, "unknown")["status"] == "unverified"
    provider.gateway_runtime["context_preflight"] = "estimate"
    assert model_status(config, provider, "ok")["count_category"] == "estimate"
    provider.gateway_runtime.pop("context_preflight")
    provider.gateway_runtime["require_current_evidence"] = True
    assert model_status(config, provider, "ok")["status"] == "unverified"
    assert model_status(config, provider, "bad")["status"] == "unsupported"
