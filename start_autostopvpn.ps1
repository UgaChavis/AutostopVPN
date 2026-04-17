param(
    [switch]$OpenReadme
)

$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot

if ($OpenReadme) {
    Start-Process -FilePath (Join-Path $scriptRoot "README.md") | Out-Null
    return
}

& (Join-Path $scriptRoot "open_amnezia_dashboard.ps1")
