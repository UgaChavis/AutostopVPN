[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$HostName = "",
    [string]$SshUser = "",
    [int]$SshPort = 0,
    [string]$KeyPath = "",
    [string]$Container = "",
    [string]$Interface = "awg0",
    [int]$Keepalive = 25,
    [string]$RollbackBackupPath = "",
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
if ($Keepalive -lt 0 -or $Keepalive -gt 65535) { throw "Keepalive must be between 0 and 65535 seconds." }

$sshKey = Resolve-SshKey -ExplicitKeyPath $KeyPath
$sshDestination = "${SshUser}@${HostName}"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$mode = if ($RollbackBackupPath) { "rollback" } else { "apply" }

$remoteScript = @'
set -euo pipefail
container="__CONTAINER__"
iface="__INTERFACE__"
keepalive="__KEEPALIVE__"
timestamp="__TIMESTAMP__"
rollback_path="__ROLLBACK_BACKUP_PATH__"
backup_dir="/root/autostopvpn-backups"
config_path="/opt/amnezia/awg/awg0.conf"
backup_path="$backup_dir/awg0.conf.keepalive.bak.$timestamp"
pre_rollback_backup_path="$backup_dir/awg0.conf.keepalive.pre-rollback.bak.$timestamp"

summarize_state() {
  docker exec "$container" sh -s -- "$iface" "$keepalive" "$config_path" <<'EOS'
set -eu
iface="$1"
keepalive="$2"
config_path="$3"
wg show "$iface" dump | awk -v target="$keepalive" '
  BEGIN { total=0; target_count=0; off=0; other=0 }
  NR > 1 && NF >= 8 {
    total++
    if ($8 == target) target_count++
    else if ($8 == "off" || $8 == "0" || $8 == "") off++
    else other++
  }
  END {
    printf "runtime_peers_total=%d\nruntime_keepalive_target=%d\nruntime_keepalive_off=%d\nruntime_keepalive_other=%d\n", total, target_count, off, other
  }
'
awk -v target="$keepalive" '
  function flush_peer() {
    if (in_peer == 1) {
      peers++
      if (ka == target) target_count++
      else if (ka == "" || ka == "0" || ka == "off") off++
      else other++
    }
  }
  /^[[:space:]]*\[Peer\][[:space:]]*$/ { flush_peer(); in_peer=1; ka=""; next }
  /^[[:space:]]*\[/ { flush_peer(); in_peer=0; ka=""; next }
  in_peer == 1 && /^[[:space:]]*PersistentKeepalive[[:space:]]*=/ {
    value=$0
    sub(/^[^=]*=/, "", value)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
    ka=value
  }
  END {
    flush_peer()
    printf "config_peers_total=%d\nconfig_keepalive_target=%d\nconfig_keepalive_off=%d\nconfig_keepalive_other=%d\n", peers, target_count, off, other
  }
' "$config_path"
EOS
}

apply_keepalive() {
  mkdir -p "$backup_dir"
  docker cp "$container:$config_path" "$backup_path"

  docker exec "$container" sh -s -- "$iface" "$keepalive" "$config_path" <<'EOS'
set -eu
iface="$1"
keepalive="$2"
config_path="$3"
tmp="$(mktemp)"

awk -v keepalive="$keepalive" '
  function ensure_keepalive() {
    if (in_peer == 1 && seen_keepalive != 1) {
      print "PersistentKeepalive = " keepalive
    }
  }
  /^[[:space:]]*\[Peer\][[:space:]]*$/ {
    ensure_keepalive()
    in_peer=1
    seen_keepalive=0
    print
    next
  }
  /^[[:space:]]*\[/ {
    ensure_keepalive()
    in_peer=0
    seen_keepalive=0
    print
    next
  }
  in_peer == 1 && /^[[:space:]]*PersistentKeepalive[[:space:]]*=/ {
    print "PersistentKeepalive = " keepalive
    seen_keepalive=1
    next
  }
  { print }
  END { ensure_keepalive() }
' "$config_path" > "$tmp"

cat "$tmp" > "$config_path"
rm -f "$tmp"

wg show "$iface" peers | while IFS= read -r peer; do
  [ -n "$peer" ] || continue
  wg set "$iface" peer "$peer" persistent-keepalive "$keepalive"
done
EOS

  echo "backup_path=$backup_path"
  echo "keepalive_apply_completed=true"
}

rollback_keepalive() {
  if [ ! -f "$rollback_path" ]; then
    echo "rollback_backup_missing=$rollback_path" >&2
    exit 2
  fi

  mkdir -p "$backup_dir"
  docker cp "$container:$config_path" "$pre_rollback_backup_path"
  rollback_tmp="/tmp/awg0.conf.keepalive.rollback.$timestamp"
  docker cp "$rollback_path" "$container:$rollback_tmp"

  docker exec "$container" sh -s -- "$iface" "$rollback_tmp" "$config_path" <<'EOS'
set -eu
iface="$1"
rollback_tmp="$2"
config_path="$3"
peer_map="$(mktemp)"

awk '
  function emit_peer() {
    if (public_key != "") {
      if (keepalive == "") keepalive="0"
      print public_key "\t" keepalive
    }
  }
  /^[[:space:]]*\[Peer\][[:space:]]*$/ {
    emit_peer()
    public_key=""
    keepalive=""
    in_peer=1
    next
  }
  /^[[:space:]]*\[/ {
    emit_peer()
    public_key=""
    keepalive=""
    in_peer=0
    next
  }
  in_peer == 1 && /^[[:space:]]*PublicKey[[:space:]]*=/ {
    value=$0
    sub(/^[^=]*=/, "", value)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
    public_key=value
    next
  }
  in_peer == 1 && /^[[:space:]]*PersistentKeepalive[[:space:]]*=/ {
    value=$0
    sub(/^[^=]*=/, "", value)
    gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
    keepalive=value
    next
  }
  END { emit_peer() }
' "$rollback_tmp" > "$peer_map"

while IFS="$(printf '\t')" read -r public_key keepalive; do
  [ -n "$public_key" ] || continue
  [ -n "$keepalive" ] || keepalive=0
  wg set "$iface" peer "$public_key" persistent-keepalive "$keepalive"
done < "$peer_map"

cat "$rollback_tmp" > "$config_path"
rm -f "$peer_map" "$rollback_tmp"
EOS

  echo "rollback_backup_path=$rollback_path"
  echo "pre_rollback_backup_path=$pre_rollback_backup_path"
  echo "keepalive_rollback_completed=true"
}

if [ -n "$rollback_path" ]; then
  rollback_keepalive
else
  apply_keepalive
fi

summarize_state
'@

$remoteScript = $remoteScript.
    Replace("__CONTAINER__", $Container).
    Replace("__INTERFACE__", $Interface).
    Replace("__KEEPALIVE__", "$Keepalive").
    Replace("__TIMESTAMP__", $timestamp).
    Replace("__ROLLBACK_BACKUP_PATH__", $RollbackBackupPath)

$remoteCommand = ConvertTo-RemoteCommand -Script $remoteScript

Write-Warning "This helper changes live WireGuard peer keepalive and the live container config. Avoid running it during provider instability."
Write-Host "target=${sshDestination}:$SshPort container=$Container interface=$Interface keepalive=$Keepalive mode=$mode key=$sshKey dry_run=$($DryRun.IsPresent) what_if=$($WhatIfPreference)"
if ($RollbackBackupPath) {
    Write-Host "rollback_backup_path=$RollbackBackupPath"
}

$sshBaseArgs = @(
    "-i", $sshKey,
    "-p", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=accept-new",
    $sshDestination
)

$label = if ($RollbackBackupPath) { "Restore keepalive backup" } else { "Apply Telegram keepalive $Keepalive" }
Invoke-GuardedNativeCommand -Label $label -FilePath "ssh" -Arguments ($sshBaseArgs + @($remoteCommand)) -Target "${sshDestination}:$SshPort"

Write-Host "Telegram keepalive helper completed."
