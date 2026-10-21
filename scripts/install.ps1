$ErrorActionPreference = 'Stop'

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-SupportedPython {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        foreach ($version in @('3.13', '3.12', '3.11')) {
            & $py.Source "-$version" -c 'import sys; print(sys.executable)' 2>$null | Out-Null
            if ($LASTEXITCODE -eq 0) {
                $pythonPath = (& $py.Source "-$version" -c 'import sys; print(sys.executable)').Trim()
                if ($pythonPath) { return $pythonPath }
            }
        }
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python) {
        & $python.Source -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>$null
        if ($LASTEXITCODE -eq 0) { return $python.Source }
    }
    return $null
}

function Add-UserPath([string]$Directory) {
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($current -split ';' | Where-Object { $_ })
    if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') })) {
        $entries += $Directory
        [Environment]::SetEnvironmentVariable('Path', ($entries -join ';'), 'User')
    }
    if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') })) {
        $env:Path = "$Directory;$env:Path"
    }
}

try {
    $repoRoot = Split-Path -Parent $PSScriptRoot
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot 'pyproject.toml'))) {
        throw 'Could not find pyproject.toml. Run this script from the project checkout; keep the scripts folder in place.'
    }

    Write-Host 'This setup installs Codex CLI and codex-ai for your Windows user only.' -ForegroundColor White
    Write-Host 'It may download Python and software from the official Codex installer. It does not request administrator access.'
    Write-Host 'At the end, the local setup page will ask you to configure provider details and API keys.'
    if ((Read-Host 'Continue? [y/N]') -notmatch '^(y|yes)$') { return }

    Write-Step 'Checking Python 3.11 or newer'
    $pythonExe = Get-SupportedPython
    if (-not $pythonExe) {
        $winget = Get-Command winget -ErrorAction SilentlyContinue
        if (-not $winget) {
            throw 'Python 3.11+ is not installed and winget is unavailable. Install Python 3.11+ from https://www.python.org/downloads/windows/, then run this installer again.'
        }
        Write-Host 'Installing Python 3.13 for the current Windows user through winget...'
        & $winget.Source install --id Python.Python.3.13 --exact --scope user --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw "Python installation failed (winget exit code $LASTEXITCODE)." }
        $pythonExe = Get-SupportedPython
        if (-not $pythonExe) {
            throw 'Python was installed, but this terminal cannot discover it yet. Close this window, open a new PowerShell window, and run install.ps1 again.'
        }
    }
    Write-Host "Using Python: $pythonExe"

    Write-Step 'Installing the official Codex CLI'
    $codexCommand = Get-Command codex -ErrorAction SilentlyContinue
    if (-not $codexCommand) {
        $installerPath = Join-Path ([IO.Path]::GetTempPath()) ("codex-install-{0}.ps1" -f [guid]::NewGuid())
        try {
            Invoke-WebRequest -Uri 'https://chatgpt.com/codex/install.ps1' -OutFile $installerPath
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installerPath
            if ($LASTEXITCODE -ne 0) { throw "Official Codex installer failed (exit code $LASTEXITCODE)." }
        }
        finally {
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        }
    }
    $codexBin = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin'
    Add-UserPath $codexBin
    if (-not (Get-Command codex -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $codexBin 'codex.exe'))) {
        throw 'Codex installer completed, but codex.exe was not found. Open a new terminal and verify the official Codex installation before rerunning setup.'
    }

    Write-Step 'Installing codex-ai in a dedicated user Python environment'
    $installRoot = Join-Path $env:LOCALAPPDATA 'Programs\codex-ai'
    $venv = Join-Path $installRoot 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
        & $pythonExe -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the codex-ai Python environment.' }
    }
    # Install from the local checkout; an editable build is unnecessary for end users.
    & $venvPython -m pip install --disable-pip-version-check --upgrade $repoRoot
    if ($LASTEXITCODE -ne 0) { throw 'Installing codex-ai failed. Check the Python/pip output and rerun this setup file.' }
    $venvScripts = Join-Path $venv 'Scripts'
    Add-UserPath $venvScripts

    Write-Step 'Launching codex-ai setup'
    & (Join-Path $venvScripts 'codex-ai.exe') setup
    if ($LASTEXITCODE -ne 0) { throw "The setup page exited with code $LASTEXITCODE." }

    Write-Host "`nInstallation complete. Open a new CMD, PowerShell, or Windows Terminal tab before using codex-ai globally." -ForegroundColor Green
    Write-Host 'You can rerun this script later to update codex-ai.'
}
catch {
    Write-Host "`nSetup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
