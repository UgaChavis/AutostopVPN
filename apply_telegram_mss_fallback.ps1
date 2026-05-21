[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$HostName = "",
    [string]$SshUser = "",
    [int]$SshPort = 0,
    [string]$KeyPath = "",
    [string]$Container = "",
    [string]$Interface = "awg0",
    [int]$Mss = 1240,
    [switch]$NoRestart,
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

function ConvertTo-RemoteCommand {
    param([Parameter(Mandatory = $true)][string]$Script)

    $remoteBytes = [System.Text.Encoding]::UTF8.GetBytes($Script)
    $remoteBase64 = [Convert]::ToBase64String($remoteBytes)
    return "printf '%s' '$remoteBase64' | base64 -d | bash"
}

$serverInfo = Resolve-ServerInfo

if (-not $HostName) { $HostName = [string]$serverInfo.public_ip }
if (-not $SshUser) { $SshUser = [string]$serverInfo.ssh_user }
if ($SshPort -le 0) { $SshPort = [int]$serverInfo.ssh_port }
if (-not $Container) { $Container = [string]$serverInfo.vpn_container }

if (-not $HostName) { throw "HostName is required." }
if (-not $SshUser) { $SshUser = "root" }
if ($SshPort -le 0) { $SshPort = 22 }
if (-not $Container) { $Container = "amnezia-awg2" }
if (-not $Interface) { $Interface = "awg0" }
if ($Mss -lt 536 -or $Mss -gt 1460) { throw "MSS must be between 536 and 1460." }

$sshKey = Resolve-SshKey -ExplicitKeyPath $KeyPath
$sshDestination = "${SshUser}@${HostName}"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$remoteScript = @'
set -euo pipefail
container="__CONTAINER__"
iface="__INTERFACE__"
mss="__MSS__"
timestamp="__TIMESTAMP__"
backup_dir="/root/autostopvpn-backups"
start_path="/opt/amnezia/start.sh"

echo "container=$container interface=$iface mss=$mss no_restart=true"
mkdir -p "$backup_dir"
docker cp "$container:$start_path" "$backup_dir/start.sh.bak.$timestamp"

apply_rule() {
  direction_name="$1"
  direction_flag="$2"
  if docker exec "$container" iptables -t mangle -C FORWARD "$direction_flag" "$iface" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$mss" 2>/dev/null; then
    echo "runtime_rule_${direction_name}=present"
  else
    docker exec "$container" iptables -t mangle -A FORWARD "$direction_flag" "$iface" -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss "$mss"
    echo "runtime_rule_${direction_name}=added"
  fi
}

apply_rule "in" "-i"
apply_rule "out" "-o"

docker exec "$container" sh -s -- "$iface" "$mss" <<'EOS'
set -eu
iface="$1"
mss="$2"
start_path="/opt/amnezia/start.sh"
begin="# AUTOSTOPVPN TELEGRAM MSS FALLBACK BEGIN"
end="# AUTOSTOPVPN TELEGRAM MSS FALLBACK END"
tmp="$(mktemp)"
block_file="$(mktemp)"

cat > "$block_file" <<BLOCK
$begin
iptables -t mangle -C FORWARD -i $iface -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $mss 2>/dev/null || iptables -t mangle -A FORWARD -i $iface -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $mss
iptables -t mangle -C FORWARD -o $iface -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $mss 2>/dev/null || iptables -t mangle -A FORWARD -o $iface -p tcp --tcp-flags SYN,RST SYN -j TCPMSS --set-mss $mss
$end
BLOCK

awk -v begin="$begin" -v end="$end" -v block_file="$block_file" '
  $0 == begin { skip=1; next }
  $0 == end { skip=0; next }
  skip == 1 { next }
  inserted != 1 && $0 ~ /^tail[[:space:]]+-f[[:space:]]+\/dev\/null/ {
    while ((getline line < block_file) > 0) { print line }
    close(block_file)
    inserted=1
  }
  { print }
  END {
    if (inserted != 1) {
      while ((getline line < block_file) > 0) { print line }
      close(block_file)
    }
  }
' "$start_path" > "$tmp"

cat "$tmp" > "$start_path"
rm -f "$tmp" "$block_file"
chmod +x "$start_path" 2>/dev/null || true
EOS

docker exec "$container" iptables -t mangle -vnL FORWARD --line-numbers | grep TCPMSS || true
echo "mss_fallback_completed=true no_restart_performed=true"
'@

$remoteScript = $remoteScript.
    Replace("__CONTAINER__", $Container).
    Replace("__INTERFACE__", $Interface).
    Replace("__MSS__", "$Mss").
    Replace("__TIMESTAMP__", $timestamp)

$remoteCommand = ConvertTo-RemoteCommand -Script $remoteScript

Write-Warning "This helper changes live container firewall rules and the container start script. Use only after the mobile pilot confirms MSS fallback is needed."
Write-Host "target=${sshDestination}:$SshPort container=$Container interface=$Interface mss=$Mss key=$sshKey dry_run=$($DryRun.IsPresent) what_if=$($WhatIfPreference) no_restart=$($NoRestart.IsPresent)"
if (-not $NoRestart) {
    Write-Warning "NoRestart was not specified. The helper still performs no restart by design; pass -NoRestart to make that intent explicit."
}

$sshBaseArgs = @(
    "-i", $sshKey,
    "-p", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=accept-new",
    $sshDestination
)

Invoke-GuardedNativeCommand -Label "Apply Telegram MSS fallback $Mss" -FilePath "ssh" -Arguments ($sshBaseArgs + @($remoteCommand)) -Target "${sshDestination}:$SshPort"

Write-Host "Telegram MSS fallback helper completed."
