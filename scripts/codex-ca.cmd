@echo off
setlocal
set "PYTHONPATH=%~dp0..\src;%PYTHONPATH%"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -m codex_adapter.cli %*
) else (
  python -m codex_adapter.cli %*
)
exit /b %ERRORLEVEL%
