[CmdletBinding()]
param(
    [string]$TargetRoot = 'D:\Автостоп VPN',
    [switch]$ForceProfilePlaceholders,
    [switch]$CleanLogs
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TemplateRoot = Join-Path $RepoRoot 'usb_vpn_loader'
$Endpoint = '46.8.254.243:443'
$Mtu = '1280'
$Keepalive = '25'

$TopLevelFiles = @(
    'Start VPN.cmd',
    'Start-VPN.ps1',
    'Stop-VPN.ps1',
    'Cleanup-This-PC.ps1',
    'Check-VPN.ps1',
    'README.txt'
)

function New-DirectoryIfMissing {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function New-ProfilePlaceholder {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$ProfileName
    )

    if ((Test-Path -LiteralPath $Path) -and -not $ForceProfilePlaceholders) {
        Write-Host ("preserve profile: {0}" -f $ProfileName)
        return
    }

    $content = @"
# PLACEHOLDER: replace this whole file with a real AmneziaWG profile.
# Slot: $ProfileName
# Server: 46.8.254.243
#
# Required values for this emergency USB loader:
#   Endpoint = $Endpoint
#   MTU = $Mtu
#   PersistentKeepalive = $Keepalive
#
# Do not commit real .conf files. They contain private client keys.
#
# Example shape only:
# [Interface]
# PrivateKey = <client private key>
# Address = <client tunnel address>
# DNS = <dns server>
# MTU = $Mtu
#
# [Peer]
# PublicKey = <server public key>
# PresharedKey = <optional preshared key>
# AllowedIPs = 0.0.0.0/0, ::/0
# Endpoint = $Endpoint
# PersistentKeepalive = $Keepalive
"@

    Set-Content -LiteralPath $Path -Value $content -Encoding UTF8
    Write-Host ("write placeholder: {0}" -f $ProfileName)
}

if (-not (Test-Path -LiteralPath $TemplateRoot)) {
    throw "Template directory not found: $TemplateRoot"
}

$targetDrive = [System.IO.Path]::GetPathRoot($TargetRoot)
if (-not $targetDrive -or -not (Test-Path -LiteralPath $targetDrive)) {
    throw "Target drive is not available: $targetDrive"
}

New-DirectoryIfMissing -Path $TargetRoot
foreach ($directory in @('profiles', 'installers', 'logs')) {
    New-DirectoryIfMissing -Path (Join-Path $TargetRoot $directory)
}

foreach ($fileName in $TopLevelFiles) {
    $source = Join-Path $TemplateRoot $fileName
    $destination = Join-Path $TargetRoot $fileName
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Template file not found: $source"
    }
    Copy-Item -LiteralPath $source -Destination $destination -Force
    Write-Host ("copy: {0}" -f $fileName)
}

if ($CleanLogs) {
    $logDir = Join-Path $TargetRoot 'logs'
    Get-ChildItem -LiteralPath $logDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like '*.log' -or $_.Name -like '*.txt' } |
        Remove-Item -Force
    Write-Host 'clean: logs'
}

$profilesDir = Join-Path $TargetRoot 'profiles'
for ($index = 1; $index -le 5; $index++) {
    $profileName = 'usb-emergency-{0:D2}.conf' -f $index
    New-ProfilePlaceholder -Path (Join-Path $profilesDir $profileName) -ProfileName $profileName
}

Write-Host ''
Write-Host ("USB VPN loader is ready: {0}" -f $TargetRoot)
Write-Host 'Existing profiles and installers are preserved. Replace only placeholder .conf files when provisioning a new USB drive.'
