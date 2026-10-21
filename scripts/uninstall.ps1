$ErrorActionPreference = 'Stop'

$installRoot = Join-Path $env:LOCALAPPDATA 'Programs\codex-ai'
$venvScripts = Join-Path $installRoot 'venv\Scripts'
$configRoot = Join-Path $env:APPDATA 'codex-ai'
$cacheFile = Join-Path $env:LOCALAPPDATA 'codex-ai\catalog-cache.json'

Write-Host 'This removes the codex-ai application installed by install.ps1.' -ForegroundColor Cyan
Write-Host 'It does not remove the Codex CLI or your provider configuration/API keys.'
if ((Read-Host 'Continue? Type Y to uninstall codex-ai') -notmatch '^(y|yes)$') {
    Write-Host 'Uninstall cancelled.'
    return
}

$expectedRoot = [IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs\codex-ai')).TrimEnd('\')
$resolvedRoot = [IO.Path]::GetFullPath($installRoot).TrimEnd('\')
if ($resolvedRoot -ine $expectedRoot) {
    throw 'Safety check failed: the application path is not the expected codex-ai install directory.'
}

if (Test-Path -LiteralPath $installRoot) {
    Remove-Item -LiteralPath $installRoot -Recurse -Force
    Write-Host 'Removed codex-ai program files.'
}
else {
    Write-Host 'No codex-ai installation from this installer was found.'
}

$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
$keptEntries = @($userPath -split ';' | Where-Object {
    $_ -and $_.TrimEnd('\') -ine $venvScripts.TrimEnd('\')
})
if ($keptEntries.Count -ne @($userPath -split ';' | Where-Object { $_ }).Count) {
    [Environment]::SetEnvironmentVariable('Path', ($keptEntries -join ';'), 'User')
    $env:Path = (@($env:Path -split ';' | Where-Object {
        $_ -and $_.TrimEnd('\') -ine $venvScripts.TrimEnd('\')
    }) -join ';')
    Write-Host 'Removed the codex-ai entry from your user PATH.'
}

Write-Host "`nThe default configuration and API keys will be preserved at: $configRoot" -ForegroundColor Yellow
Write-Host 'Custom paths set through CODEX_AI_CONFIG or CODEX_AI_ENV_FILE are not modified.'
if ((Read-Host 'Also permanently delete this configuration, API keys, and model cache? Type DELETE') -ceq 'DELETE') {
    if (Test-Path -LiteralPath $configRoot) {
        $expectedConfigRoot = [IO.Path]::GetFullPath((Join-Path $env:APPDATA 'codex-ai')).TrimEnd('\')
        if ([IO.Path]::GetFullPath($configRoot).TrimEnd('\') -ine $expectedConfigRoot) {
            throw 'Safety check failed: unexpected configuration path.'
        }
        Remove-Item -LiteralPath $configRoot -Recurse -Force
        Write-Host 'Deleted the default codex-ai configuration and .env API keys.'
    }
    if (Test-Path -LiteralPath $cacheFile) {
        Remove-Item -LiteralPath $cacheFile -Force
        Write-Host 'Deleted the model catalog cache.'
    }
}
else {
    Write-Host 'Kept configuration, API keys, and model cache.'
}

Write-Host "`nUninstall complete. Open a new terminal to refresh PATH." -ForegroundColor Green
