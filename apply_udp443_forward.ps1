[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$HostName = "",
    [string]$SshUser = "",
    [int]$SshPort = 0,
    [string]$KeyPath = "",
    [string]$Container = "",
    [string]$DockerNetwork = "amnezia-dns-net",
    [int]$ListenPort = 443,
    [int]$TargetPort = 47895,
    [switch]$Rollback,
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

    $displayArguments = @($Arguments)
    if ($displayArguments.Count -gt 0) {
        $displayArguments[$displayArguments.Count - 1] = "<remote-script>"
    }
    $commandText = "$FilePath $($displayArguments -join ' ')"
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

    $normalizedScript = $Script -replace "`r`n", "`n" -replace "`r", "`n"
    $remoteBytes = [System.Text.Encoding]::UTF8.GetBytes($normalizedScript)
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
if (-not $DockerNetwork) { $DockerNetwork = "amnezia-dns-net" }
if ($ListenPort -lt 1 -or $ListenPort -gt 65535) { throw "ListenPort must be between 1 and 65535." }
if ($TargetPort -lt 1 -or $TargetPort -gt 65535) { throw "TargetPort must be between 1 and 65535." }
if ($ListenPort -eq $TargetPort) { throw "ListenPort and TargetPort must be different." }

$sshKey = Resolve-SshKey -ExplicitKeyPath $KeyPath
$sshDestination = "${SshUser}@${HostName}"
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$mode = if ($Rollback) { "rollback" } else { "apply" }

$remoteScript = @'
set -euo pipefail
container="__CONTAINER__"
network="__DOCKER_NETWORK__"
listen_port="__LISTEN_PORT__"
target_port="__TARGET_PORT__"
timestamp="__TIMESTAMP__"
mode="__MODE__"
service_name="autostopvpn-udp443-forward.service"
script_path="/usr/local/sbin/autostopvpn-udp443-forward.sh"
service_path="/etc/systemd/system/$service_name"
backup_dir="/root/autostopvpn-backups"
rule_comment="autostopvpn-udp443-forward"

find_iptables() {
  if command -v iptables-nft >/dev/null 2>&1; then
    command -v iptables-nft
  elif command -v iptables >/dev/null 2>&1; then
    command -v iptables
  else
    echo "iptables_missing=true" >&2
    exit 2
  fi
}

IPT="$(find_iptables)"

container_ip() {
  docker inspect -f "{{with index .NetworkSettings.Networks \"$network\"}}{{.IPAddress}}{{end}}" "$container" 2>/dev/null |
    awk 'NF { print; exit }'
}

fallback_container_ip() {
  docker inspect -f '{{range .NetworkSettings.Networks}}{{if .IPAddress}}{{.IPAddress}}{{"\n"}}{{end}}{{end}}' "$container" 2>/dev/null |
    awk 'NF { print; exit }'
}

resolved_container_ip() {
  ip="$(container_ip || true)"
  if [ -z "$ip" ]; then
    ip="$(fallback_container_ip || true)"
  fi
  printf '%s\n' "$ip"
}

udp_listener_lines() {
  ss -H -lunp 2>/dev/null | awk -v suffix=":$listen_port" '$4 ~ suffix "$" { print }'
}

target_listener_lines() {
  ss -H -lunp 2>/dev/null | awk -v suffix=":$target_port" '$4 ~ suffix "$" { print }'
}

check_preflight() {
  if ! docker inspect "$container" >/dev/null 2>&1; then
    echo "container_missing=$container" >&2
    exit 3
  fi

  running="$(docker inspect -f '{{.State.Running}}' "$container" 2>/dev/null || true)"
  if [ "$running" != "true" ]; then
    echo "container_running=false" >&2
    exit 4
  fi

  ip="$(resolved_container_ip)"
  if [ -z "$ip" ]; then
    echo "container_ip_missing=true network=$network" >&2
    exit 5
  fi

  conflict="$(udp_listener_lines || true)"
  if [ -n "$conflict" ]; then
    echo "udp_${listen_port}_listener_conflict=true" >&2
    printf '%s\n' "$conflict" >&2
    exit 6
  fi

  target_listener="$(target_listener_lines || true)"
  if [ -z "$target_listener" ]; then
    echo "udp_${target_port}_listener_present=false" >&2
    exit 7
  fi
}

rule_present() {
  ip="$1"
  "$IPT" -t nat -C PREROUTING -p udp --dport "$listen_port" -m comment --comment "$rule_comment" -j DNAT --to-destination "$ip:$target_port" 2>/dev/null
}

apply_forward_rule() {
  ip="$(resolved_container_ip)"
  if [ -z "$ip" ]; then
    echo "container_ip_missing=true network=$network" >&2
    exit 5
  fi

  if rule_present "$ip"; then
    echo "udp${listen_port}_dnat_rule=present"
  else
    "$IPT" -t nat -I PREROUTING 1 -p udp --dport "$listen_port" -m comment --comment "$rule_comment" -j DNAT --to-destination "$ip:$target_port"
    echo "udp${listen_port}_dnat_rule=added"
  fi
}

remove_forward_rules() {
  tmp="$(mktemp)"
  "$IPT" -t nat -S PREROUTING 2>/dev/null |
    grep -F -- "-p udp" |
    grep -F -- "--dport $listen_port" |
    grep -F -- "-j DNAT" |
    grep -F -- ":$target_port" |
    grep -F -- "$rule_comment" |
    sed 's/^-A /-D /' > "$tmp" || true

  removed=0
  while IFS= read -r rule; do
    [ -n "$rule" ] || continue
    if "$IPT" -t nat $rule 2>/dev/null; then
      removed=$((removed + 1))
    fi
  done < "$tmp"
  rm -f "$tmp"
  echo "udp${listen_port}_dnat_rules_removed=$removed"
}

show_status() {
  ip="$(resolved_container_ip || true)"
  if [ -z "$ip" ]; then ip="unknown"; fi
  rules="$("$IPT" -t nat -S PREROUTING 2>/dev/null | grep -F -- "--dport $listen_port" | grep -F -- ":$target_port" | grep -F -- "$rule_comment" || true)"
  counters="$("$IPT" -t nat -vnL PREROUTING --line-numbers 2>/dev/null | awk -v port="dpt:$listen_port" -v target=":$target_port" '$0 ~ port || $0 ~ target { print }' || true)"
  conflict="$(udp_listener_lines || true)"
  target_listener="$(target_listener_lines || true)"
  service_active="$(systemctl is-active "$service_name" 2>/dev/null || true)"
  service_enabled="$(systemctl is-enabled "$service_name" 2>/dev/null || true)"

  echo "container=$container"
  echo "docker_network=$network"
  echo "container_ip=$ip"
  echo "iptables_backend=$IPT"
  echo "udp_${listen_port}_listener_conflict=$([ -n "$conflict" ] && echo true || echo false)"
  echo "udp_${target_port}_listener_present=$([ -n "$target_listener" ] && echo true || echo false)"
  echo "udp_${listen_port}_forward_present=$([ -n "$rules" ] && echo true || echo false)"
  echo "udp_${listen_port}_service_active=${service_active:-unknown}"
  echo "udp_${listen_port}_service_enabled=${service_enabled:-unknown}"
  printf '%s\n' "$rules" | awk 'NF { print "dnat_rule=" $0 }'
  printf '%s\n' "$counters" | awk 'NF { print "dnat_counter=" $0 }'
}

install_service() {
  mkdir -p "$backup_dir"
  if [ -f "$script_path" ]; then
    cp -a "$script_path" "$backup_dir/autostopvpn-udp443-forward.sh.bak.$timestamp"
    echo "script_backup_path=$backup_dir/autostopvpn-udp443-forward.sh.bak.$timestamp"
  fi
  if [ -f "$service_path" ]; then
    cp -a "$service_path" "$backup_dir/autostopvpn-udp443-forward.service.bak.$timestamp"
    echo "service_backup_path=$backup_dir/autostopvpn-udp443-forward.service.bak.$timestamp"
  fi

  cat > "$script_path" <<'EOSCRIPT'
#!/bin/sh
set -eu
container="__CONTAINER__"
network="__DOCKER_NETWORK__"
listen_port="__LISTEN_PORT__"
target_port="__TARGET_PORT__"
rule_comment="autostopvpn-udp443-forward"

find_iptables() {
  if command -v iptables-nft >/dev/null 2>&1; then
    command -v iptables-nft
  elif command -v iptables >/dev/null 2>&1; then
    command -v iptables
  else
    echo "iptables_missing=true" >&2
    exit 2
  fi
}

IPT="$(find_iptables)"

container_ip() {
  docker inspect -f "{{with index .NetworkSettings.Networks \"$network\"}}{{.IPAddress}}{{end}}" "$container" 2>/dev/null |
    awk 'NF { print; exit }'
}

fallback_container_ip() {
  docker inspect -f '{{range .NetworkSettings.Networks}}{{if .IPAddress}}{{.IPAddress}}{{"\n"}}{{end}}{{end}}' "$container" 2>/dev/null |
    awk 'NF { print; exit }'
}

resolved_container_ip() {
  ip="$(container_ip || true)"
  if [ -z "$ip" ]; then
    ip="$(fallback_container_ip || true)"
  fi
  printf '%s\n' "$ip"
}

udp_listener_lines() {
  ss -H -lunp 2>/dev/null | awk -v suffix=":$listen_port" '$4 ~ suffix "$" { print }'
}

target_listener_lines() {
  ss -H -lunp 2>/dev/null | awk -v suffix=":$target_port" '$4 ~ suffix "$" { print }'
}

wait_for_container_ip() {
  attempts=0
  while [ "$attempts" -lt 30 ]; do
    ip="$(resolved_container_ip || true)"
    if [ -n "$ip" ]; then
      printf '%s\n' "$ip"
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  return 1
}

wait_for_target_listener() {
  attempts=0
  while [ "$attempts" -lt 30 ]; do
    if [ -n "$(target_listener_lines || true)" ]; then
      return 0
    fi
    attempts=$((attempts + 1))
    sleep 1
  done
  return 1
}

rule_present() {
  ip="$1"
  "$IPT" -t nat -C PREROUTING -p udp --dport "$listen_port" -m comment --comment "$rule_comment" -j DNAT --to-destination "$ip:$target_port" 2>/dev/null
}

apply_rule() {
  conflict="$(udp_listener_lines || true)"
  if [ -n "$conflict" ]; then
    echo "udp_${listen_port}_listener_conflict=true" >&2
    printf '%s\n' "$conflict" >&2
    exit 6
  fi

  ip="$(wait_for_container_ip)" || {
    echo "container_ip_missing=true network=$network" >&2
    exit 5
  }

  wait_for_target_listener || {
    echo "udp_${target_port}_listener_present=false" >&2
    exit 7
  }

  if rule_present "$ip"; then
    echo "udp${listen_port}_dnat_rule=present"
  else
    "$IPT" -t nat -I PREROUTING 1 -p udp --dport "$listen_port" -m comment --comment "$rule_comment" -j DNAT --to-destination "$ip:$target_port"
    echo "udp${listen_port}_dnat_rule=added"
  fi
}

remove_rule() {
  tmp="$(mktemp)"
  "$IPT" -t nat -S PREROUTING 2>/dev/null |
    grep -F -- "-p udp" |
    grep -F -- "--dport $listen_port" |
    grep -F -- "-j DNAT" |
    grep -F -- ":$target_port" |
    grep -F -- "$rule_comment" |
    sed 's/^-A /-D /' > "$tmp" || true

  removed=0
  while IFS= read -r rule; do
    [ -n "$rule" ] || continue
    if "$IPT" -t nat $rule 2>/dev/null; then
      removed=$((removed + 1))
    fi
  done < "$tmp"
  rm -f "$tmp"
  echo "udp${listen_port}_dnat_rules_removed=$removed"
}

show_status() {
  ip="$(resolved_container_ip || true)"
  if [ -z "$ip" ]; then ip="unknown"; fi
  rules="$("$IPT" -t nat -S PREROUTING 2>/dev/null | grep -F -- "--dport $listen_port" | grep -F -- ":$target_port" | grep -F -- "$rule_comment" || true)"
  echo "container=$container"
  echo "docker_network=$network"
  echo "container_ip=$ip"
  echo "iptables_backend=$IPT"
  echo "udp_${listen_port}_forward_present=$([ -n "$rules" ] && echo true || echo false)"
}

case "${1:-apply}" in
  apply)
    apply_rule
    show_status
    ;;
  remove|rollback)
    remove_rule
    show_status
    ;;
  status)
    show_status
    ;;
  *)
    echo "usage: $0 {apply|remove|status}" >&2
    exit 64
    ;;
esac
EOSCRIPT
  chmod 0755 "$script_path"

  cat > "$service_path" <<EOSERVICE
[Unit]
Description=AutostopVPN UDP ${listen_port} forward to AmneziaWG
Requires=docker.service
After=docker.service
PartOf=docker.service

[Service]
Type=oneshot
ExecStart=$script_path apply
ExecStop=$script_path remove
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target docker.service
EOSERVICE

  systemctl daemon-reload
  systemctl enable --now "$service_name" >/dev/null
  "$script_path" apply
  echo "udp${listen_port}_forward_service_installed=true"
}

rollback_service() {
  mkdir -p "$backup_dir"
  if [ -f "$script_path" ]; then
    cp -a "$script_path" "$backup_dir/autostopvpn-udp443-forward.sh.pre-rollback.bak.$timestamp"
    echo "script_pre_rollback_backup_path=$backup_dir/autostopvpn-udp443-forward.sh.pre-rollback.bak.$timestamp"
  fi
  if [ -f "$service_path" ]; then
    cp -a "$service_path" "$backup_dir/autostopvpn-udp443-forward.service.pre-rollback.bak.$timestamp"
    echo "service_pre_rollback_backup_path=$backup_dir/autostopvpn-udp443-forward.service.pre-rollback.bak.$timestamp"
  fi

  systemctl disable --now "$service_name" >/dev/null 2>&1 || true
  remove_forward_rules || true
  rm -f "$service_path" "$script_path"
  systemctl daemon-reload
  echo "udp${listen_port}_forward_rollback_completed=true"
}

echo "mode=$mode container=$container network=$network listen_port=$listen_port target_port=$target_port"

if [ "$mode" = "rollback" ]; then
  rollback_service
else
  check_preflight
  install_service
fi

show_status
'@

$remoteScript = $remoteScript.
    Replace("__CONTAINER__", $Container).
    Replace("__DOCKER_NETWORK__", $DockerNetwork).
    Replace("__LISTEN_PORT__", "$ListenPort").
    Replace("__TARGET_PORT__", "$TargetPort").
    Replace("__TIMESTAMP__", $timestamp).
    Replace("__MODE__", $mode)

$remoteCommand = ConvertTo-RemoteCommand -Script $remoteScript

Write-Warning "This helper changes host NAT rules and installs a systemd oneshot service. It does not restart or recreate the VPN container."
Write-Host "target=${sshDestination}:$SshPort container=$Container network=$DockerNetwork listen_port=$ListenPort target_port=$TargetPort mode=$mode key_resolved=true dry_run=$($DryRun.IsPresent) what_if=$($WhatIfPreference)"

$sshBaseArgs = @(
    "-i", $sshKey,
    "-p", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    "-o", "StrictHostKeyChecking=accept-new",
    $sshDestination
)

$label = if ($Rollback) { "Rollback UDP $ListenPort forward" } else { "Apply UDP $ListenPort forward" }
Invoke-GuardedNativeCommand -Label $label -FilePath "ssh" -Arguments ($sshBaseArgs + @($remoteCommand)) -Target "${sshDestination}:$SshPort"

Write-Host "UDP $ListenPort forward helper completed."
