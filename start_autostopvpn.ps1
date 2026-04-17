param(
    [switch]$OpenReadme
)

$ErrorActionPreference = "Stop"
$scriptRoot = $PSScriptRoot

if ($OpenReadme) {
    Start-Process -FilePath (Join-Path $scriptRoot "README.md") | Out-Null
    return
}

$launcher = Join-Path $scriptRoot "open_amnezia_dashboard.ps1"
if (-not (Test-Path $launcher)) {
    throw "Launcher script not found: $launcher"
}

& $launcher
