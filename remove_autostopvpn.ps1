param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\AutostopVPN"
)

$ErrorActionPreference = "Stop"

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

$installRoot = Get-FullPath $InstallRoot
$expectedRoot = Get-FullPath (Join-Path $env:LOCALAPPDATA "AutostopVPN")
if ($installRoot -ne $expectedRoot) {
    throw "Install root must be $expectedRoot"
}

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Autostop VPN.lnk"
if (Test-Path $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath -Force
}

if (Test-Path $installRoot) {
    Remove-Item -LiteralPath $installRoot -Recurse -Force
}

Write-Host "Autostop VPN removed from $installRoot"
