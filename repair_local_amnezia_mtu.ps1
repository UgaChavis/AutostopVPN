[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$InterfaceAlias = "AmneziaVPN",
    [int]$TargetMtu = 1280,
    [int]$RollbackActiveMtu = 1376,
    [string]$BackupPath = "",
    [switch]$Rollback,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Test-IsElevated {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Resolve-ServiceRegistryPath {
    return "HKLM:\SYSTEM\CurrentControlSet\Services\AmneziaWGTunnel`$AmneziaVPN"
}

function New-SecretBackupPath {
    param([switch]$CreateDirectory)

    $localAppData = $env:LOCALAPPDATA
    if (-not $localAppData) {
        $homeRoot = if ($HOME) { $HOME } else { (Get-Location).Path }
        $localAppData = Join-Path $homeRoot ".local/share"
    }
    $backupRoot = Join-Path $localAppData "AutostopVPN/secret-backups"
    if ($CreateDirectory) {
        New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
    }
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    return Join-Path $backupRoot "AmneziaWGTunnel-AmneziaVPN-$timestamp.reg"
}

function Export-ServiceBackup {
    param([Parameter(Mandatory = $true)][string]$Path)

    $regKey = "HKLM\SYSTEM\CurrentControlSet\Services\AmneziaWGTunnel`$AmneziaVPN"
    reg export $regKey $Path /y | Out-Host
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "Registry backup was not created: $Path"
    }
}

function Set-ActiveMtu {
    param(
        [Parameter(Mandatory = $true)][string]$Alias,
        [Parameter(Mandatory = $true)][int]$Mtu
    )

    netsh interface ipv4 set subinterface $Alias mtu=$Mtu store=active | Out-Host
    netsh interface ipv6 set subinterface $Alias mtu=$Mtu store=active | Out-Host
}

function Show-State {
    param([Parameter(Mandatory = $true)][string]$Alias)

    if (Get-Command Get-NetIPInterface -ErrorAction SilentlyContinue) {
        Get-NetIPInterface -InterfaceAlias $Alias -AddressFamily IPv4, IPv6 -ErrorAction SilentlyContinue |
            Select-Object InterfaceAlias, AddressFamily, NlMtu, ConnectionState |
            Format-Table -AutoSize
    } else {
        Write-Host "netipinterface_available=false"
    }

    $servicePath = Resolve-ServiceRegistryPath
    if ((Get-PSDrive -Name HKLM -ErrorAction SilentlyContinue) -and (Test-Path -LiteralPath $servicePath)) {
        $image = (Get-ItemProperty -LiteralPath $servicePath -Name ImagePath).ImagePath
        Write-Host ("service_present=true")
        Write-Host ("service_image_has_mtu_1280=" + [bool]($image -match "MTU\s*=\s*1280"))
        Write-Host ("service_image_has_mtu_1376=" + [bool]($image -match "MTU\s*=\s*1376"))
        return
    }

    Write-Host "service_present=false"
}

if ($TargetMtu -lt 576 -or $TargetMtu -gt 1500) {
    throw "TargetMtu must be between 576 and 1500."
}

Write-Host "AutostopVPN local Amnezia MTU repair"
Write-Host "interface=$InterfaceAlias target_mtu=$TargetMtu rollback=$Rollback dry_run=$DryRun"
Write-Host "no_server_changes=true no_peer_changes=true no_iptables_changes=true no_systemd_changes=true"
Show-State -Alias $InterfaceAlias

if ($DryRun -or $WhatIfPreference) {
    if ($Rollback) {
        Write-Host "planned_action=rollback"
        Write-Host "planned_backup_import=$BackupPath"
        Write-Host "planned_active_mtu=$RollbackActiveMtu"
    } else {
        $plannedBackup = if ($BackupPath) { $BackupPath } else { New-SecretBackupPath }
        Write-Host "planned_action=apply"
        Write-Host "planned_backup_path=$plannedBackup"
        Write-Host "planned_active_mtu=$TargetMtu"
        Write-Host "planned_registry_mtu_replace=MTU 1376 -> $TargetMtu"
    }
    exit 0
}

if (-not (Test-IsElevated)) {
    throw "Run this script from an elevated PowerShell session."
}

$servicePath = Resolve-ServiceRegistryPath
if (-not (Test-Path -LiteralPath $servicePath)) {
    throw "Service registry key not found: $servicePath"
}

if ($Rollback) {
    if (-not $BackupPath) {
        throw "BackupPath is required for rollback."
    }
    if (-not (Test-Path -LiteralPath $BackupPath)) {
        throw "Rollback backup not found: $BackupPath"
    }
    if ($PSCmdlet.ShouldProcess($InterfaceAlias, "Rollback local Amnezia MTU from $BackupPath")) {
        reg import $BackupPath | Out-Host
        Set-ActiveMtu -Alias $InterfaceAlias -Mtu $RollbackActiveMtu
    }
    Show-State -Alias $InterfaceAlias
    exit 0
}

if (-not $BackupPath) {
    $BackupPath = New-SecretBackupPath -CreateDirectory
}

$image = (Get-ItemProperty -LiteralPath $servicePath -Name ImagePath).ImagePath
if ($image -notmatch "MTU\s*=\s*\d+") {
    throw "Persistent ImagePath does not contain an MTU field; not changing registry."
}

if ($PSCmdlet.ShouldProcess($InterfaceAlias, "Apply local Amnezia MTU $TargetMtu")) {
    Export-ServiceBackup -Path $BackupPath
    Set-ActiveMtu -Alias $InterfaceAlias -Mtu $TargetMtu
    $newImage = [regex]::Replace($image, "MTU\s*=\s*\d+", "MTU = $TargetMtu")
    Set-ItemProperty -LiteralPath $servicePath -Name ImagePath -Value $newImage
}

Write-Host "backup_path=$BackupPath"
Write-Host "rollback_command=.\repair_local_amnezia_mtu.ps1 -Rollback -BackupPath `"$BackupPath`""
Show-State -Alias $InterfaceAlias
