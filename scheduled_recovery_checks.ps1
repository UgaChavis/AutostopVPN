param(
    [string]$RepoRoot = $PSScriptRoot,
    [int]$PingCount = 30,
    [int]$SampleSeconds = 30,
    [int]$DownloadBytes = 0,
    [int]$LocalDownloadBytes = 0,
    [double]$ServerPingIntervalSeconds = 0.5,
    [double]$MinLocalDownloadMbps = 5.0,
    [double]$WarnLocalDownloadMbps = 20.0,
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"
$script:HealthFailures = 0
$script:HealthWarnings = 0

function Add-HealthFailure {
    param([string]$Reason)

    $script:HealthFailures += 1
    Write-Host "HEALTH_FAIL $Reason" -ForegroundColor Red
}

function Add-HealthWarning {
    param([string]$Reason)

    $script:HealthWarnings += 1
    Write-Host "HEALTH_WARN $Reason" -ForegroundColor Yellow
}

function Write-Section {
    param([string]$Title)

    $line = "=" * 72
    Write-Host ""
    Write-Host $line
    Write-Host $Title
    Write-Host $line
}

function Invoke-LoggedCommand {
    param(
        [string]$Title,
        [scriptblock]$Command
    )

    Write-Section $Title
    try {
        & $Command
        if ($null -ne $LASTEXITCODE -and $LASTEXITCODE -ne 0) {
            Write-Host "Exit code: $LASTEXITCODE" -ForegroundColor Yellow
            Add-HealthFailure "$Title exited with $LASTEXITCODE"
        }
    }
    catch {
        Write-Host $_.Exception.Message -ForegroundColor Red
        if ($_.InvocationInfo) {
            Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor DarkGray
        }
        Add-HealthFailure "$Title threw an exception"
    }
}

function Assert-PingHealthy {
    param(
        [string[]]$Output,
        [string]$Target,
        [switch]$WarningOnly
    )

    $text = ($Output | Out-String)
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $percentMatches = [regex]::Matches($text, "(?i)(?<loss>[0-9]+(?:[.,][0-9]+)?)%\s*(?:packet\s+loss|loss|потер)")
    if ($percentMatches.Count -gt 0) {
        foreach ($match in $percentMatches) {
            $loss = [double]::Parse($match.Groups["loss"].Value.Replace(",", "."), $culture)
            if ($loss -ne 0) {
                $message = "Ping health failed for ${Target}: reported ${loss}% loss."
                if ($WarningOnly) {
                    Add-HealthWarning $message
                    return
                }
                throw $message
            }
        }
        return
    }

    $lostMatches = [regex]::Matches($text, "(?i)(?:Lost|потеряно)\s*=\s*(?<lost>[0-9]+)")
    if ($lostMatches.Count -gt 0) {
        foreach ($match in $lostMatches) {
            $lost = [int]$match.Groups["lost"].Value
            if ($lost -ne 0) {
                $message = "Ping health failed for ${Target}: reported $lost lost packets."
                if ($WarningOnly) {
                    Add-HealthWarning $message
                    return
                }
                throw $message
            }
        }
        return
    }

    $message = "Ping health failed for ${Target}: could not confirm zero packet loss."
    if ($WarningOnly) {
        Add-HealthWarning $message
        return
    }
    throw $message
}

function Assert-TextContains {
    param(
        [string]$Text,
        [string]$Pattern,
        [string]$Reason
    )

    if ($Text -notmatch $Pattern) {
        throw $Reason
    }
}

function Assert-ServerMonitorHealthy {
    param([string[]]$Output)

    $text = ($Output | Out-String)
    Assert-TextContains -Text $text -Pattern "server_awg0_mtu=1280 ok=true" -Reason "Server awg0 MTU is not confirmed at 1280."
    Assert-TextContains -Text $text -Pattern "server_config_mtu=1280 ok=true" -Reason "Server config MTU is not confirmed at 1280."
    Assert-TextContains -Text $text -Pattern "udp_443_forward_present=true" -Reason "UDP 443 forward is not confirmed."
    Assert-TextContains -Text $text -Pattern "udp_443_service_active=active" -Reason "UDP 443 forward service is not active."
    Assert-TextContains -Text $text -Pattern "generic_awg0_mss_in=1" -Reason "Inbound generic MSS clamp is not confirmed."
    Assert-TextContains -Text $text -Pattern "generic_awg0_mss_out=1" -Reason "Outbound generic MSS clamp is not confirmed."
    Assert-TextContains -Text $text -Pattern "telegram_api_https_ok=true" -Reason "Server Telegram API HTTPS did not pass after retries."
    Assert-TextContains -Text $text -Pattern "openai_api_https_ok=true" -Reason "Server OpenAI API HTTPS did not pass after retries."
    Assert-TextContains -Text $text -Pattern "chatgpt_https_reachable=true" -Reason "Server ChatGPT HTTPS reachability was not confirmed."
    if ($text -match "jitter_warning=true") {
        Add-HealthWarning "Provider gateway jitter warning is active; no hard failure without packet loss."
    }
    $culture = [Globalization.CultureInfo]::InvariantCulture
    $section = ""
    foreach ($line in ($text -split "\r?\n")) {
        if ($line -match "^==\s+(?<section>.+?)\s+==$") {
            $section = $matches["section"]
            continue
        }
        $match = [regex]::Match($line, "(?i)(?<loss>[0-9]+(?:[.,][0-9]+)?)%\s+packet loss")
        if (-not $match.Success) {
            continue
        }
        $loss = [double]::Parse($match.Groups["loss"].Value.Replace(",", "."), $culture)
        if ($loss -eq 0) {
            continue
        }
        $message = "Server monitor reported ${loss}% packet loss in ${section}."
        if ($section -eq "Ping 8.8.8.8") {
            Add-HealthWarning $message
        }
        else {
            throw $message
        }
    }
}

function Invoke-TelegramHttpsProbe {
    $lastOutput = @()
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $output = @(curl.exe -4 -sS --connect-timeout 10 --max-time 15 -o NUL -w "telegram_https attempt=$attempt http=%{http_code} ip=%{remote_ip} namelookup=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total} speed=%{speed_download}`n" "https://api.telegram.org/" 2>&1)
        $output
        $lastOutput = $output
        if (($output | Out-String) -match "telegram_https attempt=$attempt http=(200|302)") {
            return
        }
        Start-Sleep -Seconds 2
    }

    Assert-TextContains -Text ($lastOutput | Out-String) -Pattern "telegram_https attempt=3 http=(200|302)" -Reason "Telegram HTTPS did not return HTTP 200 or 302 after 3 attempts."
}

function Invoke-OpenAiHttpsProbe {
    $lastOutput = @()
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $output = @(curl.exe -4 -sS --connect-timeout 10 --max-time 15 -o NUL -w "openai_api_https attempt=$attempt http=%{http_code} ip=%{remote_ip} namelookup=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}`n" "https://api.openai.com/v1/models" 2>&1)
        $output
        $lastOutput = $output
        if (($output | Out-String) -match "openai_api_https attempt=$attempt http=(200|401|403)") {
            break
        }
        Start-Sleep -Seconds 2
    }

    Assert-TextContains -Text ($lastOutput | Out-String) -Pattern "openai_api_https attempt=\d+ http=(200|401|403)" -Reason "OpenAI API HTTPS did not return HTTP 200, 401, or 403 after retries."

    $chatgptOutput = @(curl.exe -4 -sS --connect-timeout 10 --max-time 15 -o NUL -w "chatgpt_https http=%{http_code} ip=%{remote_ip} namelookup=%{time_namelookup} connect=%{time_connect} tls=%{time_appconnect} ttfb=%{time_starttransfer} total=%{time_total}`n" "https://chatgpt.com/" 2>&1)
    $chatgptOutput
    Assert-TextContains -Text ($chatgptOutput | Out-String) -Pattern "chatgpt_https http=(200|301|302|403)" -Reason "ChatGPT HTTPS reachability was not confirmed."
}

function Invoke-LocalDownloadProbe {
    if ($LocalDownloadBytes -le 0) {
        Write-Host "local_download skipped=true"
        return
    }

    $url = "https://speed.cloudflare.com/__down?bytes=$LocalDownloadBytes"
    Write-Host "local_download bytes_requested=$LocalDownloadBytes warn_mbps=$WarnLocalDownloadMbps fail_mbps=$MinLocalDownloadMbps"
    $output = @(curl.exe -4 -L -A "Mozilla/5.0" --connect-timeout 15 --max-time 120 -o NUL -w "local_download http=%{http_code} ip=%{remote_ip} connect=%{time_connect} ttfb=%{time_starttransfer} total=%{time_total} bytes=%{size_download} speed_Bps=%{speed_download}`n" $url 2>&1)
    $output
    $text = ($output | Out-String)
    Assert-TextContains -Text $text -Pattern "local_download http=200" -Reason "Local VPN download probe did not return HTTP 200."
    $match = [regex]::Match($text, "speed_Bps=(?<speed>[0-9]+)")
    if (-not $match.Success) {
        throw "Local VPN download probe did not report speed_Bps."
    }

    $speedBps = [double]$match.Groups["speed"].Value
    $speedMbps = ($speedBps * 8.0) / 1000000.0
    Write-Host ("local_download_mbps={0:N2}" -f $speedMbps)
    if ($speedMbps -lt $MinLocalDownloadMbps) {
        throw ("Local VPN download too slow: {0:N2} Mbps below fail threshold {1:N2} Mbps." -f $speedMbps, $MinLocalDownloadMbps)
    }
    if ($speedMbps -lt $WarnLocalDownloadMbps) {
        Add-HealthWarning ("Local VPN download below warning threshold: {0:N2} Mbps below {1:N2} Mbps." -f $speedMbps, $WarnLocalDownloadMbps)
    }
}

function Resolve-SshKey {
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

    return $null
}

function Write-LocalAmneziaState {
    Write-Host "expected_mtu=1280"
    $interfaces = @(Get-NetIPInterface -InterfaceAlias "AmneziaVPN" -AddressFamily IPv4, IPv6 -ErrorAction SilentlyContinue)
    $interfaces |
        Select-Object InterfaceAlias, AddressFamily, NlMtu, ConnectionState |
        Format-Table -AutoSize
    if (-not $interfaces -or @($interfaces | Where-Object { $_.NlMtu -ne 1280 }).Count -gt 0) {
        throw "Active AmneziaVPN interface MTU must be 1280."
    }

    $servicePath = "HKLM:\SYSTEM\CurrentControlSet\Services\AmneziaWGTunnel`$AmneziaVPN"
    if (Test-Path $servicePath) {
        $image = (Get-ItemProperty -LiteralPath $servicePath -Name ImagePath).ImagePath
        $hasMtu1280 = [bool]($image -match "MTU\s*=\s*1280")
        $hasMtu1376 = [bool]($image -match "MTU\s*=\s*1376")
        Write-Host ("service_image_has_mtu_1280=" + $hasMtu1280)
        Write-Host ("service_image_has_mtu_1376=" + $hasMtu1376)
        if (-not $hasMtu1280 -or $hasMtu1376) {
            throw "Persistent AmneziaVPN tunnel MTU must be 1280."
        }
    }
}

$repoPath = (Resolve-Path $RepoRoot).Path
$logRoot = Join-Path $env:LOCALAPPDATA "AutostopVPN\logs"
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logPath = Join-Path $logRoot "recovery-checks-$timestamp.log"

Start-Transcript -Path $logPath -Force | Out-Null
try {
    Set-Location $repoPath

    Write-Section "AutostopVPN recovery checks"
    Write-Host ("Started: " + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz"))
    Write-Host ("Repo:    " + $repoPath)
    Write-Host ("Log:     " + $logPath)
    Write-Host ("SSH key: " + ($(if (Resolve-SshKey) { "resolved" } else { "missing" })))

    Invoke-LoggedCommand -Title "Local AmneziaVPN interface" -Command {
        Write-LocalAmneziaState
    }

    Invoke-LoggedCommand -Title "Local VPN ping 1.1.1.1" -Command {
        $output = @(ping.exe -n $PingCount 1.1.1.1 2>&1)
        $output
        Assert-PingHealthy -Output $output -Target "1.1.1.1"
    }

    Invoke-LoggedCommand -Title "Local VPN ping 1.0.0.1" -Command {
        $output = @(ping.exe -n $PingCount 1.0.0.1 2>&1)
        $output
        Assert-PingHealthy -Output $output -Target "1.0.0.1"
    }

    Invoke-LoggedCommand -Title "Local comparison ping 8.8.8.8" -Command {
        $output = @(ping.exe -n $PingCount 8.8.8.8 2>&1)
        $output
        Assert-PingHealthy -Output $output -Target "8.8.8.8" -WarningOnly
    }

    Invoke-LoggedCommand -Title "Local Telegram ping" -Command {
        $output = @(ping.exe -n $PingCount api.telegram.org 2>&1)
        $output
        Assert-PingHealthy -Output $output -Target "api.telegram.org"
    }

    Invoke-LoggedCommand -Title "Local Telegram HTTPS" -Command {
        Invoke-TelegramHttpsProbe
    }

    Invoke-LoggedCommand -Title "Local OpenAI HTTPS" -Command {
        Invoke-OpenAiHttpsProbe
    }

    Invoke-LoggedCommand -Title "Local VPN download" -Command {
        Invoke-LocalDownloadProbe
    }

    Invoke-LoggedCommand -Title "Server read-only network monitor" -Command {
        $monitor = Join-Path $repoPath "check_autostopvpn_network.ps1"
        $output = @(& $monitor -PingCount $PingCount -PingIntervalSeconds $ServerPingIntervalSeconds -SampleSeconds $SampleSeconds -DownloadBytes $DownloadBytes *>&1)
        $output
        Assert-ServerMonitorHealthy -Output $output
    }

    if ($RunTests) {
        Invoke-LoggedCommand -Title "Local test suite" -Command {
            python -m unittest discover -s tests -v
        }
    }

    Write-Section "Finished"
    Write-Host ("Completed: " + (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz"))
    Write-Host ("Health warnings: " + $script:HealthWarnings)
    Write-Host ("Health failures: " + $script:HealthFailures)
    Write-Host "Log saved to:"
    Write-Host $logPath
}
finally {
    Stop-Transcript | Out-Null
}

if ($script:HealthFailures -gt 0) {
    exit 1
}
