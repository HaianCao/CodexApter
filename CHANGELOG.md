# Changelog

All notable changes to CodexApter are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

The `main` branch is the stable launcher that connects Codex directly to
Responses providers. The `integration/protoverta` branch is the gateway preview
**0.2.0a2**, which adds the optional [Protoverta](https://github.com/HaianCao/Protoverta)
gateway; it will be merged into `main` once it has been fully tested.

## [Unreleased] - gateway preview 0.2.0a2 (`integration/protoverta`)

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

See the [commit history of `main`](https://github.com/HaianCao/CodexApter/commits/main).
