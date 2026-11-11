# Changelog

All notable changes to CodexApter are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Changed
- Gateway model discovery skips catalog entries the provider reports with
  invalid data instead of failing, and says how many it skipped. Malformed
  token limits are dropped rather than retried without limits.
- Error messages and setup hints no longer mention a "preview" core.
- README: setup page screenshot and a connection diagram.
- Base URL rules and the loopback proxy bypass live in one module shared
  verbatim with the other launcher (checked for drift in CI).
- Type-checked with mypy in CI; the deadline transport keeps its own TLS
  context instead of reading a private `urllib` attribute.

### Removed
- Fallbacks for Protoverta builds older than the pinned version.

## [0.2.0] - 2026-11-10

Merges the gateway preview into `main`.

### Added
- Setup needs only the base URL, the API key and the API the provider speaks.
  The name and ID come from the address, authentication is detected (Bearer,
  or `x-api-key` when the provider needs it) and the key is checked without
  generating anything. Rejected keys, missing endpoints, providers without a
  model list and providers that accept only official CLIs are explained.
- Running `codex-ca` with no provider opens setup.
- Automatic retry by error class (rate limit, overloaded, server, network,
  stream interrupted, backend mismatch, unknown), configurable in `[retry]`:
  Protoverta retries in gateway mode, a local relay in direct mode.
  Deterministic errors (rejected key, no credit, unknown model, context too
  long, content policy, invalid request) are shown at once. Decisions are
  logged to a bounded `retry.log`.
- AgentRouter preset (direct, official-client only).
- Troubleshooting and "How CodexApter connects" sections in the README.

### Changed
- The local Protoverta gateway (0.3.0) is installed with every install and
  is the default for new providers; providers saved by 0.1 keep their mode.
  The installer verifies the gateway wheel's SHA-256 before installing it.
  `-WithGateway` is still accepted and changes nothing.
- Provider errors on model refresh are shown as one actionable sentence.
- The CLI version probe waits up to 15 s (a cold first start of the native CLI
  could exceed 3 s and silently disable version-gated features).
- The import package is `codexapter` (was `codex_adapter`), matching the
  distribution name. The `codex-ca` command is unchanged.

### Security
- The setup page cannot be framed (`frame-ancestors 'none'`, `X-Frame-Options: DENY`).

## Gateway preview (0.2.0a1, 0.2.0a2)

### Added
- Setup page presets for tested providers (OpenRouter, NVIDIA NIM, TokenHarbor and AgentRouter): the address,
  protocol and connection mode are filled in, so a new user only pastes an API
  key. The provider ID is derived from the display name.
- Model limits are taken automatically from the selected model's maximums when
  the provider reports them in its model list (context window); values set on the
  provider still win. The setup page shows the limits a session will use.
- Optional local Protoverta gateway: Chat Completions and Messages providers, pinned to Protoverta 0.3.0a11.
- Automatic reasoning policy that works with providers whose reasoning cannot
  be turned off (reasoning a route cannot carry is dropped, never invented).
- Model limits in setup (context window; Messages output limit, default 16384).
- Session-scoped auxiliary and child-model policies, verified subagent subset,
  route checks (`codex-ca doctor`), session diagnostics and capability registries.
- README badges, Protoverta links, package metadata, contributor guide and
  issue templates.

### Changed
- Runs newer Codex releases with verified settings and a warning instead of
  failing; verified on Codex CLI 0.166.0 and 0.167.0.
- Ctrl+C in non-interactive runs is left to Codex, so it can shut down
  cleanly.
- CI tests Python 3.11, 3.13 and 3.14; GitHub Actions use Node 24 releases.

### Fixed
- Adding a provider in the setup page saved a 4096-token Messages output limit
  instead of the 16384 default.
- The setup page script is syntax-checked in tests (a syntax error disables the
  whole page).
- Saved configurations containing a DEL character (U+007F) could no longer be
  loaded.
- Provider model catalogs nested deeper than 64 levels are rejected on every
  Python version (Python 3.14 no longer raised RecursionError for them).
- Setup and discovery boundaries: invalid API prefixes, stale cache writes,
  deeply nested setup requests, credential collisions and proxy handling for
  the local gateway.
- Saved keys take precedence over stale environment variables; protocol names are
  `chat`, `messages` and `responses` (legacy names still load).

## Stable launcher (`main`)

### 0.1.1 - 2026-11-09
- Failed provider requests are retried through a local relay (up to 10 times,
  60 s apart, configurable in `[retry]`).

See the [commit history of `main`](https://github.com/HaianCao/CodexApter/commits/main).
