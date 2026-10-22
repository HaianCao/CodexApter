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

Providers must support the OpenAI Responses API (`/responses`). Model discovery through `GET /models` is optional; you can enter model IDs yourself when a provider does not offer it. CodexApter does not translate Chat Completions or Anthropic Messages requests into Responses.

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

When you choose **Run Codex**, the setup page closes and Codex takes over the terminal where you launched `codex-ca setup`. The provider's API key is passed to Codex through the process environment.

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

Provider settings and API keys are stored on your computer. API keys are kept in a local `.env` file in plain text, so protect the file and never commit or share it. The setup page listens only on your own computer (`127.0.0.1`). CodexApter is a local launcher; it does not host a service or proxy your conversations.

## For contributors

```powershell
python -m pip install -e ".[test]"
python -m pytest
```

Pull requests and pushes are checked by the project's GitHub Actions workflow.

## License

CodexApter is licensed under the [MIT License](LICENSE).
