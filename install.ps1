# Existing environment only; never downloads dependencies or restarts clients.
$ErrorActionPreference = 'Stop'
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) {
    throw 'Create .venv with Python 3.11+ and install mcp_server/requirements.txt first.'
}
& $Python (Join-Path $PSScriptRoot 'scripts\install_clients.py') @args
exit $LASTEXITCODE
