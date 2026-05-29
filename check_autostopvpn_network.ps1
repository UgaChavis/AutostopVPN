param(
    [string]$HostName = "",
    [string]$SshUser = "",
    [int]$SshPort = 0,
    [string]$KeyPath = "",
    [string]$Container = "",
    [string]$Interface = "",
    [int]$PingCount = 10,
    [int]$SampleSeconds = 15,
    [int]$DownloadBytes = 0,
    [int]$DownloadTimeoutSeconds = 30,
    [int]$ConnectTimeoutSeconds = 15,
    [int]$TelegramTargetMtu = 1280,
    [int]$TelegramTargetKeepalive = 25,
    [int]$TelegramMss = 1240
)

$ErrorActionPreference = "Stop"

function Resolve-ServerInfo {
    $serverInfoPath = Join-Path $PSScriptRoot "amnezia_server_info.json"
    if (-not (Test-Path $serverInfoPath)) {
        return $null
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

    throw "SSH key not found. Set AUTOSTOPVPN_SSH_KEY or pass -KeyPath."
}

function Invoke-ReadOnlySsh {
    param(
        [string]$RemoteCommand,
        [switch]$AllowFailure
    )

    $target = "${script:SshUser}@${script:HostName}"
    $sshArgs = @(
        "-i", $script:ResolvedKeyPath,
        "-p", "$script:SshPort",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=$script:ConnectTimeoutSeconds",
        "-o", "ServerAliveInterval=5",
        "-o", "ServerAliveCountMax=1",
        $target,
        $RemoteCommand
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $nativeCommandPreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    $previousNativeCommandPreference = $null
    $output = @()
    $exitCode = 1
    try {
        $ErrorActionPreference = "Continue"
        if ($nativeCommandPreference) {
            $previousNativeCommandPreference = $PSNativeCommandUseErrorActionPreference
            $PSNativeCommandUseErrorActionPreference = $false
        }
        $rawOutput = & ssh @sshArgs 2>&1
        $output = @($rawOutput | ForEach-Object { $_.ToString() })
        $exitCode = $LASTEXITCODE
    }
    catch {
        $output = @($_.Exception.Message)
        $exitCode = 1
    }
    finally {
        if ($nativeCommandPreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeCommandPreference
        }
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -ne 0 -and -not $AllowFailure) {
        $text = ($output | Out-String).Trim()
        throw "SSH command failed with exit code ${exitCode}: $RemoteCommand`n$text"
    }

    [pscustomobject]@{
        ExitCode = $exitCode
        Output = @($output)
    }
}

function Invoke-ReadOnlyRemoteScript {
    param(
        [Parameter(Mandatory = $true)][string]$Script,
        [switch]$AllowFailure
    )

    $remoteBytes = [System.Text.Encoding]::UTF8.GetBytes($Script)
    $remoteBase64 = [Convert]::ToBase64String($remoteBytes)
    Invoke-ReadOnlySsh -RemoteCommand "printf '%s' '$remoteBase64' | base64 -d | bash" -AllowFailure:$AllowFailure
}

function Write-Section {
    param([string]$Title)

    Write-Host ""
    Write-Host "== $Title =="
}

function Write-CommandResult {
    param(
        [string]$Title,
        [pscustomobject]$Result
    )

    Write-Section $Title
    Write-Host "exit_code=$($Result.ExitCode)"
    $text = ($Result.Output | Out-String).TrimEnd()
    if ($text) {
        Write-Host $text
    }
}

function Get-RemoteEpoch {
    $result = Invoke-ReadOnlySsh -RemoteCommand "date +%s" -AllowFailure
    if ($result.ExitCode -eq 0) {
        $raw = (($result.Output | Select-Object -First 1) -as [string]).Trim()
        $parsed = 0L
        if ([long]::TryParse($raw, [ref]$parsed)) {
            return $parsed
        }
    }

    return [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
}

function Get-WgSnapshot {
    $result = Invoke-ReadOnlySsh -RemoteCommand "docker exec $script:Container wg show $script:Interface dump" -AllowFailure
    if ($result.ExitCode -ne 0) {
        return [pscustomobject]@{
            ExitCode = $result.ExitCode
            Peers = @()
            RawOutput = $result.Output
        }
    }

    $serverNow = Get-RemoteEpoch
    $peers = @()
    foreach ($line in @($result.Output | Select-Object -Skip 1)) {
        if (-not $line) {
            continue
        }

        $parts = ([string]$line) -split "`t"
        if ($parts.Count -lt 7) {
            continue
        }

        $handshake = 0L
        $rx = 0L
        $tx = 0L
        [void][long]::TryParse($parts[4], [ref]$handshake)
        [void][long]::TryParse($parts[5], [ref]$rx)
        [void][long]::TryParse($parts[6], [ref]$tx)

        $age = $null
        if ($handshake -gt 0) {
            $age = [Math]::Max(0, $serverNow - $handshake)
        }

        $peers += [pscustomobject]@{
            AllowedIp = $parts[3]
            Endpoint = $parts[2]
            HandshakeAgeSeconds = $age
            PersistentKeepalive = if ($parts.Count -ge 8) { $parts[7] } else { "off" }
            RxBytes = $rx
            TxBytes = $tx
        }
    }

    [pscustomobject]@{
        ExitCode = 0
        Peers = $peers
        RawOutput = $result.Output
    }
}

function Test-KeepaliveOff {
    param([object]$Value)

    $text = "$Value".Trim()
    return (-not $text -or $text -eq "0" -or $text -eq "off")
}

function Write-WgSummary {
    param([pscustomobject]$Snapshot)

    Write-Section "WireGuard peers"
    if ($Snapshot.ExitCode -ne 0) {
        Write-Host "wg_dump_failed=true"
        Write-Host (($Snapshot.RawOutput | Out-String).TrimEnd())
        return
    }

    $peers = @($Snapshot.Peers)
    $active180 = @($peers | Where-Object { $null -ne $_.HandshakeAgeSeconds -and $_.HandshakeAgeSeconds -le 180 })
    $active600 = @($peers | Where-Object { $null -ne $_.HandshakeAgeSeconds -and $_.HandshakeAgeSeconds -le 600 })
    $never = @($peers | Where-Object { $null -eq $_.HandshakeAgeSeconds })
    Write-Host "peers_total=$($peers.Count) active_180s=$($active180.Count) active_600s=$($active600.Count) never_handshake=$($never.Count)"

    $peers |
        Sort-Object @{ Expression = { if ($null -eq $_.HandshakeAgeSeconds) { [long]::MaxValue } else { $_.HandshakeAgeSeconds } } } |
        Select-Object -First 10 |
        ForEach-Object {
            $age = if ($null -eq $_.HandshakeAgeSeconds) { "never" } else { "$($_.HandshakeAgeSeconds)s" }
            Write-Host "peer=$($_.AllowedIp) endpoint=$($_.Endpoint) handshake_age=$age keepalive=$($_.PersistentKeepalive) rx=$($_.RxBytes) tx=$($_.TxBytes)"
        }
}

function Write-PeerKeepaliveSummary {
    param([pscustomobject]$Snapshot)

    Write-Section "Peer keepalive summary"
    if ($Snapshot.ExitCode -ne 0) {
        Write-Host "keepalive_summary_failed=true"
        return
    }

    $peers = @($Snapshot.Peers)
    $keepaliveOff = @($peers | Where-Object { Test-KeepaliveOff $_.PersistentKeepalive })
    $keepaliveOn = @($peers | Where-Object { -not (Test-KeepaliveOff $_.PersistentKeepalive) })
    $stale600 = @($peers | Where-Object { $_.Endpoint -and $_.Endpoint -ne "(none)" -and $null -ne $_.HandshakeAgeSeconds -and $_.HandshakeAgeSeconds -gt 600 })
    $stale3600 = @($peers | Where-Object { $_.Endpoint -and $_.Endpoint -ne "(none)" -and $null -ne $_.HandshakeAgeSeconds -and $_.HandshakeAgeSeconds -gt 3600 })
    $never = @($peers | Where-Object { $null -eq $_.HandshakeAgeSeconds })

    Write-Host "peers_total=$($peers.Count) keepalive_off=$($keepaliveOff.Count) keepalive_on=$($keepaliveOn.Count) target_mobile_keepalive_seconds=$script:TelegramTargetKeepalive"
    Write-Host "stale_600s_with_endpoint=$($stale600.Count) stale_3600s_with_endpoint=$($stale3600.Count) never_handshake=$($never.Count)"

    $stale600 |
        Sort-Object HandshakeAgeSeconds -Descending |
        Select-Object -First 10 |
        ForEach-Object {
            Write-Host "stale_peer=$($_.AllowedIp) endpoint=$($_.Endpoint) handshake_age=$($_.HandshakeAgeSeconds)s keepalive=$($_.PersistentKeepalive)"
        }
}

function Write-TelegramMobileReadiness {
    $remoteScript = @'
set -euo pipefail
container="__CONTAINER__"
iface="__INTERFACE__"
target_mtu="__TARGET_MTU__"
target_keepalive="__TARGET_KEEPALIVE__"

live_mtu="$(docker exec "$container" sh -c "cat /sys/class/net/$iface/mtu" 2>/dev/null || true)"
config_mtu="$(docker exec "$container" sh -c "awk -F= '/^[[:space:]]*MTU[[:space:]]*=/{gsub(/[[:space:]]/,\"\",\$2); print \$2; exit}' /opt/amnezia/awg/awg0.conf" 2>/dev/null || true)"

if [ "$live_mtu" = "$target_mtu" ]; then live_ok=true; else live_ok=false; fi
if [ "$config_mtu" = "$target_mtu" ]; then config_ok=true; else config_ok=false; fi

echo "target_client_mtu=$target_mtu"
echo "target_client_keepalive_seconds=$target_keepalive"
echo "server_awg0_mtu=${live_mtu:-unknown} ok=$live_ok"
echo "server_config_mtu=${config_mtu:-unknown} ok=$config_ok"
echo "mobile_profile_required=MTU=$target_mtu PersistentKeepalive=$target_keepalive"
'@

    $remoteScript = $remoteScript.
        Replace("__CONTAINER__", $script:Container).
        Replace("__INTERFACE__", $script:Interface).
        Replace("__TARGET_MTU__", "$script:TelegramTargetMtu").
        Replace("__TARGET_KEEPALIVE__", "$script:TelegramTargetKeepalive")

    Write-CommandResult -Title "Telegram mobile readiness" -Result (Invoke-ReadOnlyRemoteScript -Script $remoteScript -AllowFailure)
}

function Write-TelegramMssCounters {
    $remoteScript = @'
set -uo pipefail
container="__CONTAINER__"
iface="__INTERFACE__"
mss="__MSS__"

rules="$(docker exec "$container" iptables -t mangle -S FORWARD 2>/dev/null || true)"
counters="$(docker exec "$container" iptables -t mangle -vnL FORWARD --line-numbers 2>/dev/null || true)"

telegram_rules="$(printf '%s\n' "$rules" | grep -E '(91\.108\.0\.0/16|149\.154\.0\.0/16)' | grep -c 'TCPMSS' || true)"
generic_in="$(printf '%s\n' "$rules" | grep -F -- "-i $iface" | grep -F -- "--set-mss $mss" | grep -Ev '(91\.108\.0\.0/16|149\.154\.0\.0/16)' | wc -l | tr -d ' ')"
generic_out="$(printf '%s\n' "$rules" | grep -F -- "-o $iface" | grep -F -- "--set-mss $mss" | grep -Ev '(91\.108\.0\.0/16|149\.154\.0\.0/16)' | wc -l | tr -d ' ')"

echo "telegram_mss_rules_count=$telegram_rules"
echo "generic_awg0_mss_in=$generic_in"
echo "generic_awg0_mss_out=$generic_out"
echo "fallback_target_mss=$mss"
printf '%s\n' "$counters" | awk '/TCPMSS/ {print}'
'@

    $remoteScript = $remoteScript.
        Replace("__CONTAINER__", $script:Container).
        Replace("__INTERFACE__", $script:Interface).
        Replace("__MSS__", "$script:TelegramMss")

    Write-CommandResult -Title "Telegram MSS counters" -Result (Invoke-ReadOnlyRemoteScript -Script $remoteScript -AllowFailure)
}

function Write-TelegramApiAvailability {
    $telegramPingCount = [Math]::Min([Math]::Max($script:PingCount, 1), 20)
    $remoteCommand = "getent ahostsv4 api.telegram.org | head -n 3 || true; curl -4 -sS -L --max-time $script:DownloadTimeoutSeconds -o /dev/null -w 'http_code=%{http_code} remote_ip=%{remote_ip} time_namelookup=%{time_namelookup} time_connect=%{time_connect} time_appconnect=%{time_appconnect} time_starttransfer=%{time_starttransfer} time_total=%{time_total} speed_download=%{speed_download}\n' https://api.telegram.org/ || true; ping -4 -c $telegramPingCount -i 0.2 api.telegram.org | tail -n 4 || true"
    Write-CommandResult -Title "Telegram API availability" -Result (Invoke-ReadOnlySsh -RemoteCommand $remoteCommand -AllowFailure)
}

function Write-GatewayJitter {
    param(
        [string]$Gateway,
        [pscustomobject]$PingResult
    )

    Write-Section "Gateway jitter"
    if (-not $Gateway) {
        Write-Host "gateway=unknown"
        return
    }

    $text = ($PingResult.Output | Out-String)
    $loss = "unknown"
    $min = "unknown"
    $avg = "unknown"
    $max = "unknown"
    $mdev = "unknown"
    $lossMatch = [regex]::Match($text, "([0-9.]+)% packet loss")
    if ($lossMatch.Success) {
        $loss = $lossMatch.Groups[1].Value
    }
    $rttMatch = [regex]::Match($text, "rtt min/avg/max/(?:mdev|stddev) = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms")
    if ($rttMatch.Success) {
        $min = $rttMatch.Groups[1].Value
        $avg = $rttMatch.Groups[2].Value
        $max = $rttMatch.Groups[3].Value
        $mdev = $rttMatch.Groups[4].Value
    }

    Write-Host "gateway=$Gateway packet_loss_percent=$loss rtt_min_ms=$min rtt_avg_ms=$avg rtt_max_ms=$max rtt_mdev_ms=$mdev"
    if ($max -ne "unknown" -and [double]$max -ge 100) {
        Write-Host "jitter_warning=true reason=gateway_rtt_max_ge_100ms"
    }
    elseif ($mdev -ne "unknown" -and [double]$mdev -ge 20) {
        Write-Host "jitter_warning=true reason=gateway_rtt_mdev_ge_20ms"
    }
    else {
        Write-Host "jitter_warning=false"
    }
}

function Write-TrafficDelta {
    if ($script:SampleSeconds -le 0) {
        return
    }

    Write-Section "Traffic delta"
    $start = Get-WgSnapshot
    if ($start.ExitCode -ne 0) {
        Write-Host "traffic_delta_failed=true"
        return
    }

    Start-Sleep -Seconds $script:SampleSeconds
    $finish = Get-WgSnapshot
    if ($finish.ExitCode -ne 0) {
        Write-Host "traffic_delta_failed=true"
        return
    }

    $started = @{}
    foreach ($peer in $start.Peers) {
        $started[$peer.AllowedIp] = $peer
    }

    $rxDelta = 0L
    $txDelta = 0L
    $active = @()
    foreach ($peer in $finish.Peers) {
        if (-not $started.ContainsKey($peer.AllowedIp)) {
            continue
        }

        $previous = $started[$peer.AllowedIp]
        $drx = [Math]::Max(0, $peer.RxBytes - $previous.RxBytes)
        $dtx = [Math]::Max(0, $peer.TxBytes - $previous.TxBytes)
        $rxDelta += $drx
        $txDelta += $dtx
        if ($drx -gt 0 -or $dtx -gt 0) {
            $active += [pscustomobject]@{
                AllowedIp = $peer.AllowedIp
                RxDelta = $drx
                TxDelta = $dtx
                TotalDelta = $drx + $dtx
            }
        }
    }

    $elapsed = [double]$script:SampleSeconds
    $total = $rxDelta + $txDelta
    Write-Host ("sample_seconds={0} rx_bytes={1} tx_bytes={2} total_bytes={3} total_Bps={4:N2} active_peers_with_delta={5}" -f $script:SampleSeconds, $rxDelta, $txDelta, $total, ($total / $elapsed), $active.Count)
    $active |
        Sort-Object TotalDelta -Descending |
        Select-Object -First 10 |
        ForEach-Object {
            Write-Host "peer=$($_.AllowedIp) rx_delta=$($_.RxDelta) tx_delta=$($_.TxDelta)"
        }
}

$serverInfo = Resolve-ServerInfo
if ($serverInfo) {
    if (-not $HostName) { $HostName = [string]$serverInfo.public_ip }
    if (-not $SshUser) { $SshUser = [string]$serverInfo.ssh_user }
    if ($SshPort -le 0) { $SshPort = [int]$serverInfo.ssh_port }
    if (-not $Container) { $Container = [string]$serverInfo.vpn_container }
    if (-not $Interface) { $Interface = "awg0" }
}

if (-not $HostName) { throw "HostName is required." }
if (-not $SshUser) { $SshUser = "root" }
if ($SshPort -le 0) { $SshPort = 22 }
if (-not $Container) { $Container = "amnezia-awg2" }
if (-not $Interface) { $Interface = "awg0" }

$script:HostName = $HostName
$script:SshUser = $SshUser
$script:SshPort = $SshPort
$script:Container = $Container
$script:Interface = $Interface
$script:SampleSeconds = $SampleSeconds
$script:PingCount = $PingCount
$script:ConnectTimeoutSeconds = $ConnectTimeoutSeconds
$script:ResolvedKeyPath = Resolve-SshKey -ExplicitKeyPath $KeyPath
$script:DownloadTimeoutSeconds = $DownloadTimeoutSeconds
$script:TelegramTargetMtu = $TelegramTargetMtu
$script:TelegramTargetKeepalive = $TelegramTargetKeepalive
$script:TelegramMss = $TelegramMss

Write-Host "AutostopVPN read-only network check"
Write-Host "target=${SshUser}@${HostName}:$SshPort container=$Container interface=$Interface key=$script:ResolvedKeyPath"
Write-Host "no_restart=true no_peer_changes=true no_mtu_changes=true no_iptables_changes=true"

Write-CommandResult -Title "Server clock" -Result (Invoke-ReadOnlySsh -RemoteCommand "date -Is" -AllowFailure)
Write-CommandResult -Title "Service state" -Result (Invoke-ReadOnlySsh -RemoteCommand "systemctl is-active amnezia-dashboard.service amnezia-traffic-collector.timer amnezia-traffic-collector.service" -AllowFailure)
Write-CommandResult -Title "VPN listener" -Result (Invoke-ReadOnlySsh -RemoteCommand "docker ps --filter name=$Container --format '{{.Names}} {{.Status}} {{.Ports}}'; ss -lunp | grep 47895 || true" -AllowFailure)
$routeResult = Invoke-ReadOnlySsh -RemoteCommand "ip route get 1.1.1.1" -AllowFailure
Write-CommandResult -Title "Route to internet" -Result $routeResult
$routeText = ($routeResult.Output | Out-String)
$gatewayMatch = [regex]::Match($routeText, "\svia\s+([0-9.]+)\s")
if ($gatewayMatch.Success) {
    $gateway = $gatewayMatch.Groups[1].Value
    $gatewayPingResult = Invoke-ReadOnlySsh -RemoteCommand "ping -c $PingCount -i 0.2 $gateway" -AllowFailure
    Write-CommandResult -Title "Ping provider gateway $gateway" -Result $gatewayPingResult
    Write-GatewayJitter -Gateway $gateway -PingResult $gatewayPingResult
}

Write-CommandResult -Title "Ping 1.1.1.1" -Result (Invoke-ReadOnlySsh -RemoteCommand "ping -c $PingCount -i 0.2 1.1.1.1" -AllowFailure)
Write-CommandResult -Title "Ping 8.8.8.8" -Result (Invoke-ReadOnlySsh -RemoteCommand "ping -c $PingCount -i 0.2 8.8.8.8" -AllowFailure)
$wgSnapshot = Get-WgSnapshot
Write-WgSummary -Snapshot $wgSnapshot
Write-TelegramMobileReadiness
Write-PeerKeepaliveSummary -Snapshot $wgSnapshot
Write-TelegramMssCounters
Write-TelegramApiAvailability
Write-TrafficDelta

if ($DownloadBytes -gt 0) {
    $downloadCommand = "curl -4 -L --max-time $DownloadTimeoutSeconds -o /dev/null -w 'http_code=%{http_code} remote_ip=%{remote_ip} time_connect=%{time_connect} time_starttransfer=%{time_starttransfer} time_total=%{time_total} speed_download=%{speed_download}\n' 'https://speed.cloudflare.com/__down?bytes=$DownloadBytes'"
    Write-CommandResult -Title "Optional download sample" -Result (Invoke-ReadOnlySsh -RemoteCommand $downloadCommand -AllowFailure)
}
