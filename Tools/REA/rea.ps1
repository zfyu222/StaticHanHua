#!/usr/bin/env pwsh
# Project-local REA entry point; pass CLI arguments unchanged.
$ErrorActionPreference = 'Stop'
$env:REA_ANALYSIS_PROVIDER = 'ghidra'
$env:GHIDRA_INSTALL_DIR = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../Ghidra/ghidra_12.1.4_PUBLIC'))
$env:JAVA_HOME = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../Java/jdk-21.0.12.1+1'))
$env:PATH = "$env:JAVA_HOME/bin;" + $env:PATH
$nodePath = Join-Path $PSScriptRoot 'runtime/node-v24.21.0-win-x64/node.exe'
$entryPath = Join-Path $PSScriptRoot 'node_modules/rea-agents/scripts/rea.mjs'
foreach ($required in @($nodePath, $entryPath, "$env:JAVA_HOME/bin/java.exe", "$env:GHIDRA_INSTALL_DIR/support/analyzeHeadless.bat")) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing REA dependency: $required. See Tools/REA/README.md." }
}
& $nodePath $entryPath @args
exit $LASTEXITCODE
