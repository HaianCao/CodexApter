param([switch]$SkipCodexInstall, [switch]$NonInteractive, [switch]$NoPathChanges)
$ErrorActionPreference = 'Stop'
$sourceTempRoot = $null

function Write-Step([string]$Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Test-CodexApterSource([string]$Directory) {
    $project = Join-Path $Directory 'pyproject.toml'
    if (-not (Test-Path -LiteralPath $project)) { return $false }
    return [bool]([regex]::IsMatch((Get-Content -LiteralPath $project -Raw), '(?m)^name\s*=\s*"codexapter"\s*$'))
}

function Get-SupportedPython {
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        $installed = & $py.Source -0p 2>$null
        foreach ($line in $installed) {
            $match = [regex]::Match([string]$line, '^\s*-\S+\s+(?:\*\s+)?(.+?pythonw?\.exe)\s*$', 'IgnoreCase')
            if ($match.Success) {
                $pythonPath = $match.Groups[1].Value.Trim()
                if (Test-Path -LiteralPath $pythonPath) {
                    & $pythonPath -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>$null
                    if ($LASTEXITCODE -eq 0) { return $pythonPath }
                }
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
    if ($NoPathChanges) { return }
    if (-not (Test-Path -LiteralPath $Directory)) { return }
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($current -split ';' | Where-Object { $_ })
    if (-not ($entries | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') })) {
        $entries = @($Directory) + $entries
        [Environment]::SetEnvironmentVariable('Path', ($entries -join ';'), 'User')
    }
    if (-not (($env:Path -split ';') | Where-Object { $_.TrimEnd('\') -ieq $Directory.TrimEnd('\') })) {
        $env:Path = "$Directory;$env:Path"
    }
}

function Remove-UserPath([string]$Directory) {
    if ($NoPathChanges) { return }
    $current = [Environment]::GetEnvironmentVariable('Path', 'User')
    $entries = @($current -split ';' | Where-Object { $_ })
    $kept = @($entries | Where-Object { $_.TrimEnd('\') -ine $Directory.TrimEnd('\') })
    if ($kept.Count -ne $entries.Count) {
        [Environment]::SetEnvironmentVariable('Path', ($kept -join ';'), 'User')
    }
    $env:Path = (@($env:Path -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ine $Directory.TrimEnd('\') }) -join ';')
}

try {
    $repoRoot = $null
    if ($PSScriptRoot) {
        $candidate = Split-Path -Parent $PSScriptRoot
        if (Test-CodexApterSource $candidate) { $repoRoot = $candidate }
    }
    if (-not $repoRoot) {
        $candidate = (Get-Location).Path
        if (Test-CodexApterSource $candidate) { $repoRoot = $candidate }
    }

    Write-Host 'This installs or updates CodexApter (codex-ca) for your Windows user.' -ForegroundColor White
    Write-Host 'Missing Python or the official coding CLI may be installed. No administrator access is requested.'
    if (-not $repoRoot) { Write-Host 'The CodexApter source will be downloaded from the project GitHub repository.' }
    if (-not $NonInteractive -and (Read-Host 'Continue? [y/N]') -notmatch '^(y|yes)$') { return }

    if (-not $repoRoot) {
        Write-Step 'Downloading CodexApter source from GitHub'
        $sourceTempRoot = Join-Path ([IO.Path]::GetTempPath()) ("codexapter-source-{0}" -f [guid]::NewGuid())
        $archivePath = "$sourceTempRoot.zip"
        New-Item -ItemType Directory -Path $sourceTempRoot -Force | Out-Null
        try {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/HaianCao/CodexApter/archive/refs/heads/main.zip' -OutFile $archivePath
            Expand-Archive -LiteralPath $archivePath -DestinationPath $sourceTempRoot -Force
        }
        finally {
            Remove-Item -LiteralPath $archivePath -Force -ErrorAction SilentlyContinue
        }
        $projectFile = Get-ChildItem -LiteralPath $sourceTempRoot -Filter 'pyproject.toml' -File -Recurse | Where-Object { Test-CodexApterSource $_.Directory.FullName } | Select-Object -First 1
        if (-not $projectFile) { throw 'Downloaded archive does not contain a CodexApter project.' }
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

    Write-Step 'Checking the official Codex CLI'
    $codexCommand = Get-Command codex -ErrorAction SilentlyContinue
    if ($SkipCodexInstall) { Write-Host 'Skipped official CLI installation (-SkipCodexInstall).' }
    elseif ($codexCommand) { Write-Host 'Official CLI is already available; left unchanged.' }
    if (-not $codexCommand -and -not $SkipCodexInstall) {
        Write-Step 'Installing the official Codex CLI'
        $installerPath = Join-Path ([IO.Path]::GetTempPath()) ("codex-install-{0}.ps1" -f [guid]::NewGuid())
        try {
            Invoke-WebRequest -UseBasicParsing -Uri 'https://chatgpt.com/codex/install.ps1' -OutFile $installerPath
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installerPath
            if ($LASTEXITCODE -ne 0) { throw "Official Codex installer failed (exit code $LASTEXITCODE)." }
        }
        finally {
            Remove-Item -LiteralPath $installerPath -Force -ErrorAction SilentlyContinue
        }
    }
    $codexBin = Join-Path $env:LOCALAPPDATA 'Programs\OpenAI\Codex\bin'
    Add-UserPath $codexBin
    if (-not $SkipCodexInstall -and -not (Get-Command codex -ErrorAction SilentlyContinue) -and -not (Test-Path (Join-Path $codexBin 'codex.exe'))) {
        throw 'Codex installer completed, but codex.exe was not found. Open a new terminal and verify the official Codex installation before rerunning setup.'
    }

    $legacyInstallRoot = Join-Path $env:LOCALAPPDATA 'Programs\codex-ai'
    $legacyScripts = Join-Path $legacyInstallRoot 'venv\Scripts'
    $legacyExe = Join-Path $legacyScripts 'codex-ai.exe'
    $installRoot = Join-Path $env:LOCALAPPDATA 'Programs\codex-ca'
    $venv = Join-Path $installRoot 'venv'
    $venvPython = Join-Path $venv 'Scripts\python.exe'
    $codexCaExe = Join-Path $venv 'Scripts\codex-ca.exe'
    $isUpdate = (Test-Path -LiteralPath $codexCaExe) -or (Test-Path -LiteralPath $legacyExe)
    if ($isUpdate) {
        Write-Step 'Updating CodexApter'
    }
    else {
        Write-Step 'Installing CodexApter'
    }
    if (-not (Test-Path -LiteralPath $venvPython)) {
        New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
        & $pythonExe -m venv $venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the CodexApter Python environment.' }
    }
    # Force reinstall so source changes are applied even if pyproject.toml still has the same version.
    & $venvPython -m pip install --disable-pip-version-check --force-reinstall $repoRoot
    if ($LASTEXITCODE -ne 0) { throw 'Installing CodexApter failed. Check the Python/pip output and rerun this setup file.' }
    if (-not (Test-Path -LiteralPath $codexCaExe)) { throw 'The package installed but codex-ca.exe was not created.' }
    $venvScripts = Join-Path $venv 'Scripts'
    Add-UserPath $venvScripts

    # Trigger the application's one-time user-data migration while both old and
    # new wrapper installations are still present. Invalid config is preserved.
    & $codexCaExe config validate
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Could not validate or migrate the existing provider config. It has been preserved; run codex-ca setup to inspect it.'
    }

    # Migrate the old dedicated wrapper installation after the new executable is in place.
    if ((Test-Path -LiteralPath $legacyInstallRoot) -and ([IO.Path]::GetFullPath($legacyInstallRoot).TrimEnd('\') -ieq [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\codex-ai')).TrimEnd('\'))) {
        Remove-Item -LiteralPath $legacyInstallRoot -Recurse -Force
        Remove-UserPath $legacyScripts
        Write-Host 'Removed the old codex-ai wrapper installation. Provider settings, API keys, and Codex CLI were preserved.'
    }

    $pipx = Get-Command pipx -ErrorAction SilentlyContinue
    if ($pipx) {
        try {
            $pipxState = (& $pipx.Source list --json 2>$null | Out-String) | ConvertFrom-Json
            if ($pipxState.venvs.PSObject.Properties.Name -contains 'codex-multi-provider') {
                if (-not $NonInteractive -and (Read-Host 'A legacy pipx codex-ai wrapper was found. Remove that old wrapper too? [y/N]') -match '^(y|yes)$') {
                    & $pipx.Source uninstall codex-multi-provider
                    if ($LASTEXITCODE -ne 0) { Write-Warning 'The old pipx wrapper was not removed. It can be removed later with: pipx uninstall codex-multi-provider' }
                    else { Write-Host 'Removed the old pipx wrapper; Codex CLI and provider data were preserved.' }
                }
            }
        }
        catch {
            Write-Warning 'Could not inspect pipx for an old codex-ai wrapper.'
        }
    }

    if ($isUpdate) {
        Write-Host 'CodexApter update complete. Provider settings and API keys were preserved.' -ForegroundColor Green
    }
    else {
        Write-Host 'CodexApter installation complete.' -ForegroundColor Green
    }
    Write-Host 'Open a new CMD, PowerShell, or Windows Terminal tab and run: codex-ca setup'
}
catch {
    Write-Host "`nSetup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($sourceTempRoot) {
        $tempParent = [IO.Path]::GetFullPath((Split-Path -Parent $sourceTempRoot)).TrimEnd('\')
        $expectedParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
        if ($tempParent -ieq $expectedParent -and (Split-Path -Leaf $sourceTempRoot) -like 'codexapter-source-*' -and (Test-Path -LiteralPath $sourceTempRoot)) {
            Remove-Item -LiteralPath $sourceTempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
