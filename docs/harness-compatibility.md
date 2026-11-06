# Harness compatibility profiles

Translated Chat/Messages routes use a version-scoped policy in
`codex_adapter.harness`. Codex 0.166.0 retains `features.multi_agent=false` and
adds `agents.enabled=false`; 0.164.0 keeps the historical controls. Summary
generation and hosted web search remain disabled on these routes.

Only the selected CLI's `--version` is queried, with isolated scratch profiles,
filtered environment and a three-second timeout. Unknown or failed version
queries warn and retain the conservative legacy policy. A newer version is not
automatically certified. Native routes do not add this probe or agent policy.

The 0.166.0 profile was checked with local feature inspection and a synthetic
Chat-upstream source-launcher smoke on 2026-11-03. It does not certify subagent
execution, all model features or real providers. Explicit user CLI overrides
remain user-controlled; the wrapper policy is not an enforcement boundary.

## Agent model policy

New providers created in setup use `agent_models = { policy = "selected" }`.
Existing configs without the field and programmatic Providers keep `inherit`.
The Advanced editor shows the saved policy before Run and offers:

- `inherit`: preserve the harness's existing agent model/effort settings.
- `selected`: default agent model to the model chosen at launch; agent effort
  defaults to launch effort, or explicit low/medium/high.
- `custom`: supply an exact `default` model ID and explicit low/medium/high
  `effort`. This avoids inheriting an unknown effort from a different model.

Non-inherited policies, translated subagents and `CODEX_CA_MODEL_CATALOG` are
verified with Codex 0.166.0 and 0.167.0 (loopback client smoke). Newer 0.x
releases keep the same settings and print a warning, because Codex installs and
updates independently of the wrapper. Unknown, older or next-major versions fail
the explicit override during preflight, with an option to choose inherit.
Native routes therefore query the version too when an override is requested.
Custom agent files and explicit spawn settings retain CLI precedence over these
defaults; the wrapper does not promise to force every agent. User-supplied CLI
overrides remain available. Protocol-converted routes still disable agent tools.

No persistent agent files or config are edited. Capability evidence for another
model and actual subagent execution are separate acceptance gates. The policy
uses the launch selection; it does not dynamically follow a later `/model` switch.
