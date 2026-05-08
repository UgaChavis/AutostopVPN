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
    [int]$ConnectTimeoutSeconds = 15
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
            Write-Host "peer=$($_.AllowedIp) endpoint=$($_.Endpoint) handshake_age=$age rx=$($_.RxBytes) tx=$($_.TxBytes)"
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
$script:ConnectTimeoutSeconds = $ConnectTimeoutSeconds
$script:ResolvedKeyPath = Resolve-SshKey -ExplicitKeyPath $KeyPath

Write-Host "AutostopVPN read-only network check"
Write-Host "target=${SshUser}@${HostName}:$SshPort container=$Container interface=$Interface key=$script:ResolvedKeyPath"
Write-Host "no_restart=true no_peer_changes=true no_mtu_changes=true"

Write-CommandResult -Title "Server clock" -Result (Invoke-ReadOnlySsh -RemoteCommand "date -Is" -AllowFailure)
Write-CommandResult -Title "Service state" -Result (Invoke-ReadOnlySsh -RemoteCommand "systemctl is-active amnezia-dashboard.service amnezia-traffic-collector.timer amnezia-traffic-collector.service" -AllowFailure)
Write-CommandResult -Title "VPN listener" -Result (Invoke-ReadOnlySsh -RemoteCommand "docker ps --filter name=$Container --format '{{.Names}} {{.Status}} {{.Ports}}'; ss -lunp | grep 47895 || true" -AllowFailure)
Write-CommandResult -Title "Route to internet" -Result (Invoke-ReadOnlySsh -RemoteCommand "ip route get 1.1.1.1" -AllowFailure)

$routeResult = Invoke-ReadOnlySsh -RemoteCommand "ip route get 1.1.1.1" -AllowFailure
$routeText = ($routeResult.Output | Out-String)
$gatewayMatch = [regex]::Match($routeText, "\svia\s+([0-9.]+)\s")
if ($gatewayMatch.Success) {
    $gateway = $gatewayMatch.Groups[1].Value
    Write-CommandResult -Title "Ping provider gateway $gateway" -Result (Invoke-ReadOnlySsh -RemoteCommand "ping -c $PingCount -i 0.2 $gateway" -AllowFailure)
}

Write-CommandResult -Title "Ping 1.1.1.1" -Result (Invoke-ReadOnlySsh -RemoteCommand "ping -c $PingCount -i 0.2 1.1.1.1" -AllowFailure)
Write-CommandResult -Title "Ping 8.8.8.8" -Result (Invoke-ReadOnlySsh -RemoteCommand "ping -c $PingCount -i 0.2 8.8.8.8" -AllowFailure)
Write-WgSummary -Snapshot (Get-WgSnapshot)
Write-TrafficDelta

if ($DownloadBytes -gt 0) {
    $downloadCommand = "curl -4 -L --max-time $DownloadTimeoutSeconds -o /dev/null -w 'http_code=%{http_code} remote_ip=%{remote_ip} time_connect=%{time_connect} time_starttransfer=%{time_starttransfer} time_total=%{time_total} speed_download=%{speed_download}\n' 'https://speed.cloudflare.com/__down?bytes=$DownloadBytes'"
    Write-CommandResult -Title "Optional download sample" -Result (Invoke-ReadOnlySsh -RemoteCommand $downloadCommand -AllowFailure)
}
