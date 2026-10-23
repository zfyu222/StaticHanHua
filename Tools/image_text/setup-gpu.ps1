$ErrorActionPreference = 'Stop'
$taskPython = Join-Path $PSScriptRoot '.venv-gpu/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    & python -m venv (Join-Path $PSScriptRoot '.venv-gpu')
    if ($LASTEXITCODE -ne 0) { throw 'Creating GPU environment failed' }
}
& $taskPython -m pip install -r (Join-Path $PSScriptRoot 'requirements-gpu-lock.txt')
if ($LASTEXITCODE -ne 0) { throw 'Installing GPU dependencies failed' }
