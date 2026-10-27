$taskPython = Join-Path $PSScriptRoot '.venv-gpu/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run setup-gpu.ps1 first' }
if ($args.Count -gt 0 -and $args[0] -eq 'pipeline') {
    & $taskPython (Join-Path $PSScriptRoot 'image_workflow.py') @args
} else {
    & $taskPython (Join-Path $PSScriptRoot 'cli.py') @args
}
exit $LASTEXITCODE
