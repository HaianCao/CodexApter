"""Session-only model defaults; never enables Codex agent tools."""

from .errors import ConfigError, LaunchError
from .harness import detect_version
from .toml_text import toml_value


def _model(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 512 or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value):
        raise ConfigError("Agent model must be an exact model ID without whitespace (1–512 characters).")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise ConfigError("Agent model must contain valid Unicode.") from None
    return value


def validate_agent_models(value):
    if not isinstance(value, dict) or set(value) - {"policy", "default", "effort"}:
        raise ConfigError("agent_models accepts policy, default and effort only.")
    policy = value.get("policy", "inherit")
    if policy not in ("inherit", "selected", "custom"):
        raise ConfigError("Agent model policy must be inherit, selected or custom.")
    if policy == "inherit" and set(value) - {"policy"}:
        raise ConfigError("Inherited agent policy cannot contain model or effort overrides.")
    if policy == "selected" and "default" in value:
        raise ConfigError("Selected agent policy uses the launch model; choose custom for another model.")
    if "default" in value:
        _model(value["default"])
    if value.get("effort", "inherit") not in ("inherit", "low", "medium", "high"):
        raise ConfigError("Agent effort must be inherit, low, medium or high.")
    if policy == "custom" and ("default" not in value or value.get("effort") not in ("low", "medium", "high")):
        raise ConfigError("Custom Codex agent models require an exact default model and explicit low/medium/high effort.")
    return dict(value)


def agent_overrides(prefix, options, model, effort):
    options = validate_agent_models(options)
    if options.get("policy", "inherit") == "inherit":
        return []
    if detect_version(prefix) != (0, 166, 0):
        raise LaunchError("Agent model overrides are verified with Codex 0.166.0 only. Choose Inherit harness settings for another version.")
    selected = _model(options.get("default", model))
    requested_effort = options.get("effort", "inherit")
    requested_effort = effort if requested_effort == "inherit" else requested_effort
    if requested_effort not in ("low", "medium", "high"):
        raise LaunchError("Choose low, medium or high effort for the agent model.")
    return [f"agents.default_subagent_model={toml_value(selected)}",
            f"agents.default_subagent_reasoning_effort={toml_value(requested_effort, ensure_ascii=True)}"]


def agent_models_toml(options):
    return "{ " + ", ".join(f"{key} = {toml_value(value)}" for key, value in sorted(validate_agent_models(options).items())) + " }"
