# Codex Multi-Provider Adapter

`codex-ai` lets you configure multiple API providers and launch Codex with your chosen provider, model, and reasoning level.

## Install on Windows

Run this in PowerShell to download and install directly from GitHub without cloning the repository:

```powershell
& ([scriptblock]::Create((irm "https://raw.githubusercontent.com/HaianCao/free-codex-cli/main/scripts/install.ps1")))
```

The installer downloads the project source and installs the required tools for your Windows account, then opens the configuration page. Add your provider and API key there. Open a new terminal when setup finishes so it can find `codex-ai` on PATH. You can also run `scripts/install.ps1` from a local checkout.

Run the same command again any time to update `codex-ai` to the latest code on GitHub. Updates keep your existing configuration and API keys; the setup page opens only on first installation. This does not update the Codex CLI itself. An older installed `codex-ai` keeps working if you do not update.

Requirements: Windows 10/11 and an internet connection. If Python 3.11+ is missing, the script uses `winget` to install it. The script does not require administrator access or change the system-wide PATH.

## Configure

In the browser page:

1. Add a provider name and its API base URL, such as `https://api.example.com/v1`.
2. Enter your API key.
3. Refresh the model list, or type a model ID manually.
4. Choose a default model and reasoning level, then save.

The provider must support the OpenAI Responses API (`/responses`). This project does not convert Chat Completions or Anthropic Messages requests into Responses.

## Use

```powershell
codex-ai                         # launch with saved defaults
codex-ai setup                   # reopen configuration
codex-ai run -p PROVIDER -m MODEL
codex-ai exec -p PROVIDER -m MODEL -- "Explain this repository"
codex-ai resume -p PROVIDER -- --last
codex-ai providers list
codex-ai doctor -p PROVIDER -m MODEL
```

Run `codex-ai --help` for all commands. You can launch `codex-ai` from any directory after installation.

## Data and security

Configuration is saved under `%APPDATA%\codex-ai`; API keys are stored in a local `.env` file and are not written to the TOML configuration. The `.env` file contains plaintext secrets: do not share or commit it. The setup page listens only on your own computer (`127.0.0.1`).

## Development

```powershell
python -m pip install -e ".[test]"
python -m pytest
```
