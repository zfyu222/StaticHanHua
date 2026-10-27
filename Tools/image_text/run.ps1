$taskPython = Join-Path $PSScriptRoot '.venv-gpu/Scripts/python.exe'
if ($args -contains '--allow-nonstandard-ocr') { $taskPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe' }
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Required environment missing. Run setup-gpu.ps1; do not silently switch to CPU.' }
if ($args.Count -gt 0 -and $args[0] -eq 'pipeline') {
    & $taskPython (Join-Path $PSScriptRoot 'image_workflow.py') @args
} else {
    & $taskPython (Join-Path $PSScriptRoot 'cli.py') @args
}
exit $LASTEXITCODE
