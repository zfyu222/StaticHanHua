$ErrorActionPreference = 'Stop'
$taskVersion = '0.40.9'
$taskDownloads = Join-Path $PSScriptRoot 'downloads'
$taskRuntime = Join-Path $PSScriptRoot 'runtime'
$taskZip = Join-Path $taskDownloads "ollama-windows-amd64-v$taskVersion.zip"
$taskExecutable = Join-Path $taskRuntime 'ollama.exe'
$taskMetadata = Join-Path $PSScriptRoot 'installation.json'
if (Test-Path -LiteralPath $taskExecutable) {
    if (Test-Path -LiteralPath $taskMetadata) {
        $taskInstalled = Get-Content -LiteralPath $taskMetadata -Raw | ConvertFrom-Json
        $taskHash = (Get-FileHash -LiteralPath $taskExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($taskInstalled.version -eq $taskVersion -and $taskInstalled.executable_sha256 -eq $taskHash) {
            Write-Output "Portable Ollama $taskVersion already installed and verified"
            return
        }
    }
    throw 'Existing runtime differs from installation metadata; preserve it before selecting a new install'
}
New-Item -ItemType Directory -Path $taskDownloads -Force | Out-Null
if (-not (Test-Path -LiteralPath $taskZip)) {
    & curl.exe --fail --location "https://github.com/ollama/ollama/releases/download/v$taskVersion/ollama-windows-amd64.zip" --output $taskZip
    if ($LASTEXITCODE -ne 0) { throw 'Portable Ollama download failed; check incomplete ZIP before retry' }
}
if (Test-Path -LiteralPath $taskRuntime) { throw 'Runtime already exists; preserve it or select a new installation directory' }
Expand-Archive -LiteralPath $taskZip -DestinationPath $taskRuntime
$taskHash = (Get-FileHash -LiteralPath $taskExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
@{ version=$taskVersion; source="https://github.com/ollama/ollama/releases/download/v$taskVersion/ollama-windows-amd64.zip"; executable_sha256=$taskHash } |
    ConvertTo-Json | Set-Content -LiteralPath $taskMetadata -Encoding utf8
