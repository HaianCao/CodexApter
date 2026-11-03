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

Model-role configuration and subagent acceptance are separate work. The policy
does not alter persistent Codex configuration or migrate session state.
