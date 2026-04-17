param(
    [string]$HostName = "46.8.254.243",
    [string]$SshUser = "root",
    [string]$KeyPath = "",
    [int]$LocalPort = 18765,
    [int]$RemotePort = 18080
)

$dashboardUrl = "http://127.0.0.1:${LocalPort}/"
$tunnelSpec = "127.0.0.1:${LocalPort}:127.0.0.1:${RemotePort}"

function Test-LocalPort {
    param([int]$Port)

    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $async = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(700)) {
            $client.Close()
            return $false
        }
        $client.EndConnect($async) | Out-Null
        $client.Close()
        return $true
    } catch {
        return $false
    }
}

if (-not $KeyPath) {
    $candidateKeys = @(
        "$env:USERPROFILE\.ssh\autostopvpn_server_ed25519",
        "$env:USERPROFILE\.ssh\autostopcrm_server_ed25519"
    )
    $KeyPath = $candidateKeys | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $KeyPath -or -not (Test-Path $KeyPath)) {
    throw "SSH key not found. Checked autostopvpn_server_ed25519 and autostopcrm_server_ed25519 in $env:USERPROFILE\.ssh"
}

$sshPath = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
if (-not (Test-Path $sshPath)) {
    $sshPath = (Get-Command ssh.exe -ErrorAction Stop).Source
}

$sshProcess = $null
$sshLog = Join-Path $env:TEMP 'amnezia-dashboard-ssh.log'
Remove-Item $sshLog -Force -ErrorAction SilentlyContinue

if (-not (Test-LocalPort -Port $LocalPort)) {
    $sshProcess = Start-Process -FilePath $sshPath -WindowStyle Minimized -PassThru -ArgumentList @(
        "-i", $KeyPath,
        "-o", "BatchMode=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=accept-new",
        "-N",
        "-L", $tunnelSpec,
        "$SshUser@$HostName"
    ) -RedirectStandardError $sshLog

    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if (Test-LocalPort -Port $LocalPort) {
            break
        }
    }
}

if (-not (Test-LocalPort -Port $LocalPort)) {
    if ($sshProcess -and $sshProcess.HasExited) {
        $tail = ""
        if (Test-Path $sshLog) {
            $tail = " " + ((Get-Content $sshLog -Tail 5 -ErrorAction SilentlyContinue) -join " ").Trim()
        }
        throw "Dashboard tunnel failed, ssh exited with code $($sshProcess.ExitCode).$tail"
    }
    throw "Dashboard tunnel is not available on $dashboardUrl"
}

Start-Process $dashboardUrl | Out-Null
