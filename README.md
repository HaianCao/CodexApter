<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/CodexApter_black.png">
    <img src="assets/CodexApter_white.png" width="224" alt="CodexApter logo">
  </picture>
  <p><strong>A clean, focused way to connect Codex to the model provider you choose.</strong></p>
  <p>Manage providers, find models, choose a reasoning level, and launch Codex from one local setup studio.</p>
  <p>
    <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/license-MIT-5a4fcf?style=flat-square"></a>
    <img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white">
    <img alt="Windows" src="https://img.shields.io/badge/platform-Windows-0078d4?style=flat-square&logo=windows&logoColor=white">
    <a href="https://github.com/HaianCao/CodexApter/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/HaianCao/CodexApter/ci.yml?branch=main&style=flat-square&label=tests"></a>
  </p>
  <p><a href="#quick-start">Quick start</a> &middot; <a href="#configure-a-provider">Providers</a> &middot; <a href="#use-codexapter">CLI</a> &middot; <a href="#privacy-and-security">Security</a></p>
</div>

---

CodexApter is a lightweight launcher and configuration studio for the official Codex CLI. It lets you connect compatible API providers, choose a model and reasoning effort, and start Codex with those settings without replacing or modifying the Codex CLI itself.

## What you can do

- Save and switch between multiple providers in a local browser-based setup page.
- Search a provider's model catalog as you type, or enter a model ID manually.
- Choose a default provider, model, and reasoning effort for future sessions.
- Add, edit, and remove provider connections, and switch between light and dark themes.
- Launch Codex in the current terminal, or select a different provider/model for one run.
- Manage API keys locally without putting them in the provider configuration file.

Responses providers connect directly by default. An opt-in local Protoverta gateway can translate the supported text/function-tool subset from Chat Completions or Anthropic Messages providers into Responses; unsupported requests fail explicitly. Model discovery through `GET /models` is optional, and model IDs can be entered manually.

## Quick start

### Windows

In PowerShell, run the installer directly from GitHub. No repository clone is needed:

```powershell
& ([scriptblock]::Create((irm "https://raw.githubusercontent.com/HaianCao/CodexApter/main/scripts/install.ps1")))
```

The installer sets up CodexApter for your Windows account. It can install Python 3.11+ with `winget` if needed and installs the official Codex CLI only if it is missing. It does not require administrator access. When installation finishes, open a new terminal and start setup:

```powershell
codex-ca setup
```

The setup page opens in your browser. Add a provider, enter its API key, refresh the model list or type a model ID, then choose your default model and reasoning effort. Setup is opened only when you run `codex-ca setup`.

## Configure a provider

In the setup page:

1. Select **Add** and enter a provider ID, display name, and API base URL (for example, `https://api.example.com/v1`).
2. Enter the provider's API key. CodexApter derives the internal secret and environment-variable names from the provider ID.
3. Select **Refresh models** to query the provider's model catalog, or type a model ID directly.
4. Choose a reasoning effort and select **Save as default** or **Run Codex**.

When you choose **Run Codex**, the setup page closes and Codex takes over the terminal where you launched `codex-ca setup`. Direct mode supplies the provider key; gateway mode supplies a temporary local token and keeps the upstream key in the launcher.

### Optional protocol gateway

In the provider editor, **Upstream reasoning** defaults to the provider/client
settings. Choose **Disabled — OpenRouter** for OpenRouter models whose default
reasoning cannot be translated, or **Disabled — standard API** for compatible
upstreams. Disabling requires gateway mode and overrides effort; models requiring
reasoning may reject it. Unsupported reasoning output is never silently hidden.

To install the opt-in gateway preview from `integration/protoverta`, run:

```powershell
& ([scriptblock]::Create((irm "https://raw.githubusercontent.com/HaianCao/CodexApter/integration/protoverta/scripts/install.ps1"))) -WithGateway -SourceBranch integration/protoverta
```

The gateway component is version-pinned. Select **Chat Completions** or **Messages** as the provider API in setup, or opt into the gateway for a Responses provider. The regular `main` installer remains the stable, non-gateway path.

Under **Advanced: model compatibility checks**, you can select a local capability
registry file and require verified capabilities. Relative paths start at the
configuration folder. Unsupported features are blocked before API calls; the
strict option also blocks missing records. Leave this optional section empty
unless you maintain capability records for your provider's exact API prefix and
model IDs. Basic launch capabilities are checked before setup closes; tools and
output formats are checked against each actual request.

Cross-protocol mode supports text, function tools and `low`/`medium`/`high` effort when the model supports it. The launcher requests web search, multi-agent execution and reasoning summaries to be disabled for translated sessions. Session history may still advertise old function tools; supported namespace functions are mapped by the gateway. Hidden reasoning history does not transfer between protocols. For Messages providers, the default output limit is 4096 tokens when Codex supplies no limit; this is adjustable in setup.

Responses function namespaces are mapped to reversible aliases for Chat/Messages;
native Responses forwarding is unchanged. In a Messages provider's advanced
settings, exact model IDs can opt into provider-verified mid-conversation system
support. Leave this empty unless support is confirmed. Instruction positions
are preserved, so incompatible Codex model-switch histories still fail instead
of being silently rewritten. Resume compatibility depends on the actual history
and provider, not only on the selected endpoint.

## Use CodexApter

```powershell
codex-ca                                      # launch using saved defaults
codex-ca setup                                # open the provider setup page
codex-ca run -p PROVIDER -m MODEL_ID          # choose a provider and model for this run
codex-ca exec -p PROVIDER -m MODEL_ID -- "Explain this repository"
codex-ca resume -p PROVIDER -- --last
codex-ca providers list
codex-ca models list -p PROVIDER
codex-ca doctor -p PROVIDER -m MODEL_ID
codex-ca --help
```

After installation, `codex-ca` is available from any working directory. Re-run the installer command to update CodexApter; your provider settings are retained.

## Uninstall

In PowerShell, run the uninstaller from GitHub:

```powershell
& ([scriptblock]::Create((irm "https://raw.githubusercontent.com/HaianCao/CodexApter/main/scripts/uninstall.ps1")))
```

The uninstaller removes the CodexApter launcher and its PATH entry. It keeps provider settings and API keys by default; you can separately confirm their deletion. The official Codex CLI is not removed.

## Privacy and security

Provider settings and API keys are stored on your computer. API keys are kept in a local `.env` file in plain text, so protect the file and never commit or share it. The setup page listens only on your own computer (`127.0.0.1`). Direct mode does not proxy conversations; gateway mode starts a temporary loopback Protoverta server and stops it when Codex exits.

Gateway mode is experimental. Client interoperability is tested with scripted local upstreams; each real provider/model still needs its own compatibility check.

## For contributors

```powershell
python -m pip install -e ".[test]"
python -m pytest
```

Pull requests and pushes are checked by the project's GitHub Actions workflow.

To test an unpublished gateway build, pass `-ProtovertaWheel PATH_TO_WHEEL` to the local installer. This enables the gateway extra and checks its pinned package version without copying Protoverta source into this project.

Installer smoke tests can pass `-NoPathChanges` to install and uninstall scripts. Redirect `APPDATA` and `LOCALAPPDATA` in a disposable child shell as well: the flag alone does not isolate installation or user data paths.

## License

CodexApter is licensed under the [MIT License](LICENSE).
