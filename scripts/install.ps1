param([switch]$SkipCodexInstall, [switch]$NonInteractive, [switch]$WithGateway, [string]$ProtovertaWheel, [switch]$NoPathChanges, [string]$SourceBranch = 'main')
$ErrorActionPreference = 'Stop'
$sourceTempRoot = $null
$wheelTempRoot = $null
$installLock = $null

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
    if ($SourceBranch -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$' -or $SourceBranch.Contains('..')) {
        throw 'SourceBranch must be a valid Git branch name.'
    }
    $repoRoot = $null
    if ($PSScriptRoot -and -not $PSBoundParameters.ContainsKey('SourceBranch')) {
        $candidate = Split-Path -Parent $PSScriptRoot
        if (Test-CodexApterSource $candidate) { $repoRoot = $candidate }
    }
    if (-not $repoRoot -and -not $PSBoundParameters.ContainsKey('SourceBranch')) {
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
            Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/HaianCao/CodexApter/archive/refs/heads/${SourceBranch}.zip" -OutFile $archivePath
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
    . (Join-Path $repoRoot 'scripts\install-environment.ps1')
    $installLock = Open-AdapterInstallLock $installRoot
    $previousPython = Get-AdapterInstalledPython $installRoot
    $isUpdate = [bool]$previousPython -or (Test-Path -LiteralPath $legacyExe)
    if ($isUpdate) {
        Write-Step 'Updating CodexApter'
    }
    else {
        Write-Step 'Installing CodexApter'
    }
    # Build a fresh generation even when the package version is unchanged.
    $installTarget = $repoRoot
    # The local Protoverta gateway is part of every install. -WithGateway is
    # still accepted from older commands and changes nothing.
    $protovertaVersion = '0.3.0'
    $protovertaSha256 = 'PENDING-RELEASE'
    if ($ProtovertaWheel) {
        if (-not (Test-Path -LiteralPath $ProtovertaWheel -PathType Leaf) -or [IO.Path]::GetExtension($ProtovertaWheel) -ne '.whl') {
            throw 'ProtovertaWheel must point to a local Protoverta wheel file.'
        }
        $gatewayPackages = @((Resolve-Path -LiteralPath $ProtovertaWheel).Path)
    }
    else {
        $wheelTempRoot = Join-Path ([IO.Path]::GetTempPath()) ("protoverta-wheel-{0}" -f [guid]::NewGuid())
        New-Item -ItemType Directory -Path $wheelTempRoot | Out-Null
        $protovertaAsset = "https://github.com/HaianCao/Protoverta/releases/download/v$protovertaVersion/protoverta-$protovertaVersion-py3-none-any.whl"
        $gatewayPackages = @(Get-AdapterVerifiedWheel -Uri $protovertaAsset -Sha256 $protovertaSha256 -Directory $wheelTempRoot)
    }
    $candidate = New-AdapterEnvironment -Root $installRoot -Python $pythonExe -Target $installTarget -GatewayPackages $gatewayPackages -Module 'codexapter' -Command 'codex-ca'
    Set-AdapterCurrentEnvironment $installRoot $candidate.Id
    Add-UserPath $candidate.Bin
    $codexCaExe = $candidate.Launcher
    # Keep the old venv and generations for existing processes; only stop advertising them.
    Remove-UserPath (Join-Path $installRoot 'venv\Scripts')

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
    Write-Host 'Open a new CMD, PowerShell, or Windows Terminal tab and run: codex-ca (setup opens the first time; codex-ca setup changes providers later)'
}
catch {
    Write-Host "`nSetup failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    if ($installLock) { $installLock.Dispose() }
    if ($wheelTempRoot -and (Test-Path -LiteralPath $wheelTempRoot)) { Remove-Item -LiteralPath $wheelTempRoot -Recurse -Force -ErrorAction SilentlyContinue }
    if ($sourceTempRoot) {
        $tempParent = [IO.Path]::GetFullPath((Split-Path -Parent $sourceTempRoot)).TrimEnd('\')
        $expectedParent = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\')
        if ($tempParent -ieq $expectedParent -and (Split-Path -Leaf $sourceTempRoot) -like 'codexapter-source-*' -and (Test-Path -LiteralPath $sourceTempRoot)) {
            Remove-Item -LiteralPath $sourceTempRoot -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
