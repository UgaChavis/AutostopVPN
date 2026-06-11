[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$HostName = "",
    [string]$SshUser = "",
    [int]$SshPort = 0,
    [string]$KeyPath = "",
    [int]$RelayPort = 10443,
    [string]$BridgeInterface = "amn0",
    [string]$ContainerBridgeIp = "172.29.172.2/32",
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
    Write-Host "$Label`: $FilePath $($displayArguments -join ' ')"
    if ($DryRun) {
        Write-Host "dry_run=True"
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

if (-not $HostName) { throw "HostName is required." }
if (-not $SshUser) { $SshUser = "root" }
if ($SshPort -le 0) { $SshPort = 22 }
if ($RelayPort -lt 1024 -or $RelayPort -gt 65535) { throw "RelayPort must be between 1024 and 65535." }
if (-not $BridgeInterface) { throw "BridgeInterface is required." }
if (-not $ContainerBridgeIp) { throw "ContainerBridgeIp is required." }

$sshKey = Resolve-SshKey -ExplicitKeyPath $KeyPath
$sshDestination = "${SshUser}@${HostName}"
$mode = if ($Rollback) { "rollback" } else { "apply" }

$remoteScript = @'
set -euo pipefail
mode="__MODE__"
relay_port="__RELAY_PORT__"
bridge_iface="__BRIDGE_IFACE__"
container_ip="__CONTAINER_IP__"
service_name="autostopvpn-telegram-relay.service"
relay_path="/usr/local/sbin/autostopvpn-telegram-relay.py"
rules_path="/usr/local/sbin/autostopvpn-telegram-relay-rules.sh"
service_path="/etc/systemd/system/$service_name"

echo "mode=$mode relay_port=$relay_port bridge_interface=$bridge_iface container_bridge_ip=$container_ip"
echo "no_amnezia_restart=true no_peer_changes=true no_mtu_changes=true no_mss_changes=true"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3_missing=true" >&2
  exit 2
fi
if ! command -v iptables >/dev/null 2>&1; then
  echo "iptables_missing=true" >&2
  exit 3
fi

write_files() {
  cat > "$relay_path" <<'PY'
#!/usr/bin/env python3
import logging
import socket
import socketserver
import struct
import threading
import time

SO_ORIGINAL_DST = 80
LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = __RELAY_PORT__
BUFFER_SIZE = 65536
CONNECT_TIMEOUT = 8
IDLE_TIMEOUT = 180

IPV4_TO_IPV6 = {
    "149.154.166.110": "2001:67c:4e8:f004::9",
    "149.154.167.40": "2001:67c:4e8:f002::a",
    "149.154.167.41": "2001:67c:4e8:f002::a",
    "149.154.167.50": "2001:67c:4e8:f002::a",
    "149.154.167.51": "2001:67c:4e8:f002::a",
    "149.154.167.91": "2001:67c:4e8:f004::a",
    "149.154.167.92": "2001:67c:4e8:f004::a",
    "149.154.167.99": "2001:67c:4e8:f004::9",
    "91.108.56.130": "2001:b28:f23f:f005::a",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

def original_destination(client):
    raw = client.getsockopt(socket.SOL_IP, SO_ORIGINAL_DST, 16)
    _family, port, packed_ip = struct.unpack_from("!HH4s", raw)
    return socket.inet_ntoa(packed_ip), port

def copy_stream(src, dst, label):
    try:
        while True:
            data = src.recv(BUFFER_SIZE)
            if not data:
                break
            dst.sendall(data)
    except OSError as exc:
        logging.debug("stream closed %s: %s", label, exc)
    finally:
        for sock, how in ((dst, socket.SHUT_WR), (src, socket.SHUT_RD)):
            try:
                sock.shutdown(how)
            except OSError:
                pass

class RelayHandler(socketserver.BaseRequestHandler):
    def handle(self):
        client = self.request
        client.settimeout(IDLE_TIMEOUT)
        try:
            original_ip, original_port = original_destination(client)
        except OSError as exc:
            logging.warning("cannot read original destination from %s: %s", self.client_address, exc)
            return
        target_ip = IPV4_TO_IPV6.get(original_ip)
        if not target_ip:
            logging.warning("no mapping for %s:%s from %s", original_ip, original_port, self.client_address)
            return
        started = time.monotonic()
        try:
            upstream = socket.create_connection((target_ip, original_port), timeout=CONNECT_TIMEOUT)
            upstream.settimeout(IDLE_TIMEOUT)
        except OSError as exc:
            logging.warning("connect failed %s:%s -> [%s]:%s: %s", original_ip, original_port, target_ip, original_port, exc)
            return
        logging.debug("relay %s:%s -> [%s]:%s client=%s", original_ip, original_port, target_ip, original_port, self.client_address[0])
        try:
            t1 = threading.Thread(target=copy_stream, args=(client, upstream, "client-to-upstream"), daemon=True)
            t2 = threading.Thread(target=copy_stream, args=(upstream, client, "upstream-to-client"), daemon=True)
            t1.start()
            t2.start()
            t1.join(IDLE_TIMEOUT)
            t2.join(IDLE_TIMEOUT)
        finally:
            try:
                upstream.close()
            except OSError:
                pass
            logging.debug("relay closed %s:%s elapsed=%.1fs", original_ip, original_port, time.monotonic() - started)

class ThreadingTCPServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True

def main():
    with ThreadingTCPServer((LISTEN_HOST, LISTEN_PORT), RelayHandler) as server:
        logging.info("AutostopVPN Telegram relay listening on %s:%s mappings=%s", LISTEN_HOST, LISTEN_PORT, len(IPV4_TO_IPV6))
        server.serve_forever()

if __name__ == "__main__":
    main()
PY

  cat > "$rules_path" <<'SH'
#!/bin/sh
set -eu
CHAIN="AUTOSTOPVPN_TG_RELAY"
PORT="__RELAY_PORT__"
BRIDGE_IFACE="__BRIDGE_IFACE__"
CONTAINER_IP="__CONTAINER_IP__"
TELEGRAM_IPS="149.154.166.110 149.154.167.40 149.154.167.41 149.154.167.50 149.154.167.51 149.154.167.91 149.154.167.92 149.154.167.99 91.108.56.130"

remove_rules() {
  iptables -t nat -D PREROUTING -i "$BRIDGE_IFACE" -s "$CONTAINER_IP" -p tcp -j "$CHAIN" 2>/dev/null || true
  iptables -t nat -D OUTPUT -p tcp -j "$CHAIN" 2>/dev/null || true
  iptables -D INPUT -i "$BRIDGE_IFACE" -s "$CONTAINER_IP" -p tcp --dport "$PORT" -j ACCEPT 2>/dev/null || true
  iptables -t nat -F "$CHAIN" 2>/dev/null || true
  iptables -t nat -X "$CHAIN" 2>/dev/null || true
}

case "${1:-apply}" in
  apply)
    remove_rules
    iptables -t nat -N "$CHAIN"
    for ip in $TELEGRAM_IPS; do
      iptables -t nat -A "$CHAIN" -d "$ip/32" -p tcp -m multiport --dports 80,443 -j REDIRECT --to-ports "$PORT"
    done
    iptables -I INPUT 1 -i "$BRIDGE_IFACE" -s "$CONTAINER_IP" -p tcp --dport "$PORT" -j ACCEPT
    iptables -t nat -I PREROUTING 1 -i "$BRIDGE_IFACE" -s "$CONTAINER_IP" -p tcp -j "$CHAIN"
    iptables -t nat -I OUTPUT 1 -p tcp -j "$CHAIN"
    ;;
  remove)
    remove_rules
    ;;
  *)
    echo "usage: $0 apply|remove" >&2
    exit 2
    ;;
esac
SH

  sed -i "s/__RELAY_PORT__/$relay_port/g; s/__BRIDGE_IFACE__/$bridge_iface/g; s#__CONTAINER_IP__#$container_ip#g" "$relay_path" "$rules_path"

  cat > "$service_path" <<SERVICE
[Unit]
Description=AutostopVPN Telegram IPv4 to IPv6 relay
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
ExecStartPre=$rules_path apply
ExecStart=$relay_path
ExecStopPost=$rules_path remove
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
SERVICE

  chmod 0755 "$relay_path" "$rules_path"
  chmod 0644 "$service_path"
  python3 -m py_compile "$relay_path"
}

show_status() {
  echo "telegram_relay_service_active=$(systemctl is-active "$service_name" 2>/dev/null || true)"
  echo "telegram_relay_service_enabled=$(systemctl is-enabled "$service_name" 2>/dev/null || true)"
  iptables -S INPUT 2>/dev/null | grep -F -- "--dport $relay_port" || true
  iptables -t nat -S PREROUTING 2>/dev/null | grep -F AUTOSTOPVPN_TG_RELAY || true
  iptables -t nat -S OUTPUT 2>/dev/null | grep -F AUTOSTOPVPN_TG_RELAY || true
  iptables -t nat -L AUTOSTOPVPN_TG_RELAY -n -v --line-numbers 2>/dev/null || true
  journalctl -u "$service_name" --no-pager -p warning -n 5 2>/dev/null || true
}

if [ "$mode" = "rollback" ]; then
  systemctl disable --now "$service_name" 2>/dev/null || true
  "$rules_path" remove 2>/dev/null || true
  rm -f "$relay_path" "$rules_path" "$service_path"
  systemctl daemon-reload
  echo "telegram_relay_removed=true"
  show_status
  exit 0
fi

write_files
systemctl daemon-reload
systemctl enable --now "$service_name"
systemctl restart "$service_name"
sleep 1
show_status
echo "telegram_relay_apply_completed=true"
'@

$remoteScript = $remoteScript.
    Replace("__MODE__", $mode).
    Replace("__RELAY_PORT__", "$RelayPort").
    Replace("__BRIDGE_IFACE__", $BridgeInterface).
    Replace("__CONTAINER_IP__", $ContainerBridgeIp)

$sshArgs = @(
    "-i", $sshKey,
    "-p", "$SshPort",
    "-o", "BatchMode=yes",
    "-o", "ConnectTimeout=15",
    $sshDestination,
    (ConvertTo-RemoteCommand -Script $remoteScript)
)

Write-Host "Apply Telegram IPv4 to IPv6 relay"
Write-Host "target=$sshDestination port=$SshPort relay_port=$RelayPort mode=$mode"
Write-Host "no_amnezia_restart=true no_peer_changes=true no_mtu_changes=true no_mss_changes=true"
Invoke-GuardedNativeCommand -Label "Apply Telegram IPv4 to IPv6 relay" -FilePath "ssh" -Arguments $sshArgs -Target $sshDestination
Write-Host "Telegram IPv4 to IPv6 relay helper completed"
