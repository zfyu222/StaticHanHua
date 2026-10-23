$taskPython = Join-Path $PSScriptRoot '.venv-gpu/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run setup-gpu.ps1 first' }
& $taskPython (Join-Path $PSScriptRoot 'cli.py') @args
exit $LASTEXITCODE
