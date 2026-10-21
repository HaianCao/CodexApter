$ErrorActionPreference = 'Stop'
$sourceTempRoot = $null

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
    $repoRoot = $null
    if ($PSScriptRoot) {
        $candidate = Split-Path -Parent $PSScriptRoot
        if (Test-Path -LiteralPath (Join-Path $candidate 'pyproject.toml')) { $repoRoot = $candidate }
    }
    if (-not $repoRoot) {
        $candidate = (Get-Location).Path
        if (Test-Path -LiteralPath (Join-Path $candidate 'pyproject.toml')) { $repoRoot = $candidate }
    }

    Write-Host 'This installer installs Codex CLI and codex-ai for your Windows user only.' -ForegroundColor White
    Write-Host 'It may download Python and software from the official Codex installer. It does not request administrator access.'
    if (-not $repoRoot) { Write-Host 'The codex-ai source will be downloaded from the project GitHub repository.' }
    Write-Host 'At the end, the local setup page will ask you to configure provider details and API keys.'
    if ((Read-Host 'Continue? [y/N]') -notmatch '^(y|yes)$') { return }

    if (-not $repoRoot) {
        Write-Step 'Downloading codex-ai source from GitHub'
        $sourceTempRoot = Join-Path ([IO.Path]::GetTempPath()) ("codex-ai-source-{0}" -f [guid]::NewGuid())
        $archivePath = "$sourceTempRoot.zip"
        New-Item -ItemType Directory -Path $sourceTempRoot -Force | Out-Null
        try {
            Invoke-WebRequest -Uri 'https://github.com/HaianCao/free-codex-cli/archive/refs/heads/main.zip' -OutFile $archivePath
            Expand-Archive -LiteralPath $archivePath -DestinationPath $sourceTempRoot -Force
        }
        finally {
            Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        }
        $projectFile = Get-ChildItem -LiteralPath $sourceTempRoot -Filter 'pyproject.toml' -File -Recurse | Select-Object -First 1
        if (-not $projectFile) { throw 'Downloaded repository does not contain pyproject.toml.' }
        $repoRoot = $projectFile.Directory.FullName
    }

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

    $installRoot = Join-Path $env:LOCALAPPDATA 'Programs\codex-ai'
    $venv = Join-Path $installRoot 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    $codexAiExe = Join-Path $venv 'Scripts\codex-ai.exe'
    $isUpdate = Test-Path -LiteralPath $codexAiExe
    if ($isUpdate) {
        Write-Step 'Updating codex-ai in the existing user Python environment'
    }
    else {
        Write-Step 'Installing codex-ai in a dedicated user Python environment'
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
        & $pythonExe -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the codex-ai Python environment.' }
    }
    # Force reinstall so source changes are applied even if pyproject.toml still has the same version.
    & $venvPython -m pip install --disable-pip-version-check --force-reinstall --no-deps $repoRoot
    if ($LASTEXITCODE -ne 0) { throw 'Installing codex-ai failed. Check the Python/pip output and rerun this setup file.' }
    $venvScripts = Join-Path $venv 'Scripts'
    Add-UserPath $venvScripts

    if ($isUpdate) {
        Write-Host 'codex-ai has been updated. Your existing provider configuration and API keys were left unchanged.' -ForegroundColor Green
        Write-Host 'Run codex-ai setup if you want to review or change your settings.'
    }
    else {
        Write-Step 'Launching codex-ai setup'
        & $codexAiExe setup
        if ($LASTEXITCODE -ne 0) { throw "The setup page exited with code $LASTEXITCODE." }
    }

    if ($isUpdate) {
        Write-Host "`ncodex-ai update complete. Open a new CMD, PowerShell, or Windows Terminal tab to use the updated command." -ForegroundColor Green
        Write-Host 'The Codex CLI itself was not updated.'
    }
    else {
        Write-Host "`ncodex-ai installation complete. Open a new CMD, PowerShell, or Windows Terminal tab to use the command." -ForegroundColor Green
    }
}
catch {
    Write-Host "`nSetup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($sourceTempRoot -and (Test-Path -LiteralPath $sourceTempRoot)) {
        Remove-Item -LiteralPath $sourceTempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
