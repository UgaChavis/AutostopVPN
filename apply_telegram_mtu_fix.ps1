[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$HostName = "",
    [string]$SshUser = "",
    [int]$SshPort = 0,
    [string]$KeyPath = "",
    [string]$Container = "",
    [int]$Mtu = 0,
    [switch]$NoServerInfoSync,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Resolve-ServerInfo {
    $serverInfoPath = Join-Path $PSScriptRoot "amnezia_server_info.json"
    if (-not (Test-Path $serverInfoPath)) {
        throw "Server info file not found: $serverInfoPath"
    }

    return Get-Content $serverInfoPath -Raw | ConvertFrom-Json
}

function Resolve-SshKey {
    param([string]$ExplicitKeyPath)

    if ($ExplicitKeyPath) {
        $resolved = Resolve-Path $ExplicitKeyPath -ErrorAction Stop
        return $resolved.Path
    }

    foreach ($envName in @("AUTOSTOPVPN_SSH_KEY", "AUTOSTOPCRM_SSH_KEY")) {
        $value = [Environment]::GetEnvironmentVariable($envName)
        if ($value -and (Test-Path $value)) {
            return (Resolve-Path $value).Path
        }
    }

    foreach ($name in @(
        "autostopvpn_server_ed25519",
        "autostopcrm_server_ed25519",
        "codex_autostopvpn",
        "codex_autostopcrm",
        "codex_autostopcrm_key"
    )) {
        $candidate = Join-Path $HOME ".ssh\$name"
        if (Test-Path $candidate) {
            return (Resolve-Path $candidate).Path
        }
    }

    throw "SSH key not found. Set AUTOSTOPVPN_SSH_KEY, AUTOSTOPCRM_SSH_KEY, pass -KeyPath, or use a standard key name in ~/.ssh."
}

function Invoke-GuardedNativeCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Label,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Target
    )

    $commandText = "$FilePath $($Arguments -join ' ')"
    Write-Host "$Label`: $commandText"
    if ($DryRun) {
        Write-Host "dry_run=true"
        return
    }

    if (-not $PSCmdlet.ShouldProcess($Target, $Label)) {
        return
    }

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE"
    }
}

$serverInfo = Resolve-ServerInfo
$serverInfoPath = Join-Path $PSScriptRoot "amnezia_server_info.json"

if (-not $HostName) { $HostName = [string]$serverInfo.public_ip }
if (-not $SshUser) { $SshUser = [string]$serverInfo.ssh_user }
if ($SshPort -le 0) { $SshPort = [int]$serverInfo.ssh_port }
if (-not $Container) { $Container = [string]$serverInfo.vpn_container }
if ($Mtu -le 0) {
    $Mtu = if ($serverInfo.wireguard_mtu) { [int]$serverInfo.wireguard_mtu } else { 1280 }
}

if (-not $HostName) { throw "HostName is required." }
if (-not $SshUser) { $SshUser = "root" }
if ($SshPort -le 0) { $SshPort = 22 }
if (-not $Container) { $Container = "amnezia-awg2" }
if ($Mtu -lt 576 -or $Mtu -gt 1500) { throw "MTU must be between 576 and 1500." }

$sshKey = Resolve-SshKey -ExplicitKeyPath $KeyPath
$sshDestination = "${SshUser}@${HostName}"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupCommand = "mkdir -p /root/autostopvpn-backups && docker cp ${Container}:/opt/amnezia/awg/awg0.conf /root/autostopvpn-backups/awg0.conf.bak.$timestamp"
$applyCommand = "docker exec ${Container} ip link set dev awg0 mtu $Mtu && docker exec ${Container} cat /sys/class/net/awg0/mtu"
$scpDestination = "${sshDestination}:/var/lib/amnezia-traffic/server_info.json"

Write-Warning "This helper changes the live VPN container MTU. Do not run it during provider instability unless you intentionally need the MTU fix."
Write-Host "target=${sshDestination}:$SshPort container=$Container mtu=$Mtu key=$sshKey dry_run=$($DryRun.IsPresent) what_if=$($WhatIfPreference)"

$sshBaseArgs = @(
    "-i", $sshKey,
    "-p", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=accept-new",
    $sshDestination
)

Invoke-GuardedNativeCommand -Label "Backup live awg0.conf" -FilePath "ssh" -Arguments ($sshBaseArgs + @($backupCommand)) -Target "${sshDestination}:$SshPort"
Invoke-GuardedNativeCommand -Label "Apply awg0 MTU $Mtu" -FilePath "ssh" -Arguments ($sshBaseArgs + @($applyCommand)) -Target "${sshDestination}:$SshPort"

if (-not $NoServerInfoSync) {
    Invoke-GuardedNativeCommand -Label "Sync dashboard server info" -FilePath "scp" -Arguments @(
        "-i", $sshKey,
        "-P", "$SshPort",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=15",
        "-o", "StrictHostKeyChecking=accept-new",
        $serverInfoPath,
        $scpDestination
    ) -Target $scpDestination
}

Write-Host "MTU helper completed."
