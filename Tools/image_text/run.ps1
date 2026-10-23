$taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run setup.ps1 first' }
& $taskPython (Join-Path $PSScriptRoot 'cli.py') @args
exit $LASTEXITCODE
