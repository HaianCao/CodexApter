param([switch]$NonInteractive)
$ErrorActionPreference = 'Stop'

# Remove both the current wrapper and the previous codex-ai wrapper, if present.
$installRoots = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\codex-ca'),
    (Join-Path $env:LOCALAPPDATA 'Programs\codex-ai')
)
$venvScripts = @($installRoots | ForEach-Object { Join-Path $_ 'venv\Scripts' })
$configRoot = Join-Path $env:APPDATA 'codexapter'
$legacyConfigRoot = Join-Path $env:APPDATA 'codex-ai'
$cacheRoot = Join-Path $env:LOCALAPPDATA 'codexapter'
$legacyCacheRoot = Join-Path $env:LOCALAPPDATA 'codex-ai'

Write-Host 'This removes CodexApter and its old codex-ai wrapper, if present.' -ForegroundColor Cyan
Write-Host 'It does not remove the Codex CLI or your provider configuration/API keys.'
if (-not $NonInteractive -and (Read-Host 'Continue? Type Y to uninstall CodexApter') -notmatch '^(y|yes)$') {
    Write-Host 'Uninstall cancelled.'
    return
}

$removedInstall = $false
foreach ($installRoot in $installRoots) {
    $expectedRoot = [IO.Path]::GetFullPath($installRoot).TrimEnd('\')
    $resolvedRoot = [IO.Path]::GetFullPath($installRoot).TrimEnd('\')
    if ($resolvedRoot -ine $expectedRoot) {
        throw 'Safety check failed: an application path is not the expected install directory.'
    }
    if (Test-Path -LiteralPath $installRoot) {
        Remove-Item -LiteralPath $installRoot -Recurse -Force
        $removedInstall = $true
    }
}
if ($removedInstall) {
    Write-Host 'Removed CodexApter wrapper program files.'
}
else {
    Write-Host 'No dedicated CodexApter installation was found.'
}

# Older releases could be installed with pipx. Remove that copy only after an
# explicit confirmation so uninstalling the managed venv does not expose a
# stale command unexpectedly.
$pipx = Get-Command pipx -ErrorAction SilentlyContinue
if ($pipx) {
    try {
        $pipxState = (& $pipx.Source list --json 2>$null | Out-String) | ConvertFrom-Json
        $installedPackages = @($pipxState.venvs.PSObject.Properties.Name)
        foreach ($packageName in @('codexapter', 'codex-multi-provider')) {
            if (($installedPackages -contains $packageName) -and -not $NonInteractive -and (Read-Host "A pipx copy of $packageName was found. Remove it too? [y/N]") -match '^(y|yes)$') {
                & $pipx.Source uninstall $packageName
                if ($LASTEXITCODE -ne 0) { Write-Warning "Could not remove $packageName. Remove it later with: pipx uninstall $packageName" }
                else { Write-Host "Removed $packageName from pipx." }
            }
        }
    }
    catch {
        Write-Warning 'Could not inspect pipx for an older CodexApter wrapper.'
    }
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$pathTargets = @($venvScripts | ForEach-Object { [IO.Path]::GetFullPath($_).TrimEnd('\') })
$keptEntries = @($userPath -split ';' | Where-Object {
    $_ -and ($pathTargets -notcontains $_.TrimEnd('\'))
})
if ($keptEntries.Count -ne @($userPath -split ';' | Where-Object { $_ }).Count) {
    [Environment]::SetEnvironmentVariable('Path', ($keptEntries -join ';'), 'User')
    $env:Path = (@($env:Path -split ';' | Where-Object {
        $_ -and ($pathTargets -notcontains $_.TrimEnd('\'))
    }) -join ';')
    Write-Host 'Removed CodexApter wrapper entries from your user PATH.'
}

Write-Host "`nThe default configuration and API keys will be preserved at: $configRoot" -ForegroundColor Yellow
if (Test-Path -LiteralPath $legacyConfigRoot) { Write-Host "Legacy configuration (if not migrated) is at: $legacyConfigRoot" -ForegroundColor Yellow }
Write-Host 'Custom paths set through CODEX_CA_CONFIG or CODEX_CA_ENV_FILE are not modified.'
if (-not $NonInteractive -and (Read-Host 'Also permanently delete this configuration, API keys, and model cache? Type DELETE') -ceq 'DELETE') {
    foreach ($dataRoot in @($configRoot, $legacyConfigRoot)) {
        if (Test-Path -LiteralPath $dataRoot) {
            $expectedRoots = @([IO.Path]::GetFullPath((Join-Path $env:APPDATA 'codexapter')).TrimEnd('\'), [IO.Path]::GetFullPath((Join-Path $env:APPDATA 'codex-ai')).TrimEnd('\'))
            if ($expectedRoots -notcontains [IO.Path]::GetFullPath($dataRoot).TrimEnd('\')) {
                throw 'Safety check failed: unexpected configuration path.'
            }
            Remove-Item -LiteralPath $dataRoot -Recurse -Force
        }
    }
    foreach ($dataRoot in @($cacheRoot, $legacyCacheRoot)) {
        if (Test-Path -LiteralPath $dataRoot) {
            $expectedRoots = @([IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'codexapter')).TrimEnd('\'), [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'codex-ai')).TrimEnd('\'))
            if ($expectedRoots -notcontains [IO.Path]::GetFullPath($dataRoot).TrimEnd('\')) {
                throw 'Safety check failed: unexpected cache path.'
            }
            Remove-Item -LiteralPath $dataRoot -Recurse -Force
        }
    }
    Write-Host 'Deleted CodexApter and legacy configuration, API keys, and model cache.'
}
else {
    Write-Host 'Kept configuration, API keys, and model cache.'
}

Write-Host "`nUninstall complete. Open a new terminal to refresh PATH." -ForegroundColor Green
