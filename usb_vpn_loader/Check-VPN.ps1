[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'

$BaseDir = Split-Path -Parent $PSCommandPath
$LogDir = Join-Path $BaseDir 'logs'
$ProgramDataRoot = Join-Path $env:ProgramData 'AutostopVPN\Emergency'
$MarkerPath = Join-Path $ProgramDataRoot 'active-tunnel.json'
$EndpointHost = '46.8.254.243'
$Endpoint = '46.8.254.243:443'
$LogPath = $null

function Write-Log {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Level = 'INFO'
    )

    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, $Message
    Write-Host $line
    if ($LogPath) {
        Add-Content -LiteralPath $LogPath -Value $line -Encoding UTF8
    }
}

function Find-AmneziaWGExecutable {
    foreach ($commandName in @('amneziawg.exe', 'amneziawg')) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            return $command.Source
        }
    }

    $programFilesX86 = ${env:ProgramFiles(x86)}
    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles 'AmneziaWG\amneziawg.exe')
        $candidates += (Join-Path $env:ProgramFiles 'AmneziaVPN\amneziawg.exe')
        $candidates += (Join-Path $env:ProgramFiles 'AmneziaVPN\AmneziaWG\amneziawg.exe')
    }
    if ($programFilesX86) {
        $candidates += (Join-Path $programFilesX86 'AmneziaWG\amneziawg.exe')
        $candidates += (Join-Path $programFilesX86 'AmneziaVPN\amneziawg.exe')
        $candidates += (Join-Path $programFilesX86 'AmneziaVPN\AmneziaWG\amneziawg.exe')
    }

    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    return $null
}

function Get-EmergencyServices {
    $services = @{}
    Get-Service -ErrorAction SilentlyContinue |
        Where-Object {
            $text = '{0} {1}' -f $_.Name, $_.DisplayName
            $text -match '(?i)(autostopvpn-emergency|usb-emergency)'
        } |
        ForEach-Object { $services[$_.Name] = $_ }

    if (Test-Path -LiteralPath $MarkerPath) {
        try {
            $marker = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
            foreach ($serviceName in @($marker.ServiceNames)) {
                if ($serviceName) {
                    $service = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
                    if ($service) {
                        $services[$service.Name] = $service
                    }
                }
            }
            if ($marker.TunnelName) {
                Get-Service -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -like "*$($marker.TunnelName)*" } |
                    ForEach-Object { $services[$_.Name] = $_ }
            }
        } catch {
            Write-Log ("Не удалось прочитать marker: {0}" -f $_.Exception.Message) 'WARN'
        }
    }

    return $services.Values
}

function Invoke-HttpCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Uri
    )

    try {
        $started = Get-Date
        $response = Invoke-WebRequest -Uri $Uri -UseBasicParsing -TimeoutSec 10
        $elapsedMs = [math]::Round(((Get-Date) - $started).TotalMilliseconds)
        Write-Log ("HTTPS {0}: OK {1} ms HTTP {2}" -f $Name, $elapsedMs, [int]$response.StatusCode)
        return $response
    } catch {
        Write-Log ("HTTPS {0}: FAIL {1}" -f $Name, $_.Exception.Message) 'WARN'
        return $null
    }
}

function Invoke-DnsCheck {
    param([Parameter(Mandatory = $true)][string]$Name)

    try {
        $result = Resolve-DnsName -Name $Name -Type A -ErrorAction Stop | Select-Object -First 3
        $addresses = @($result | ForEach-Object { $_.IPAddress }) -join ', '
        Write-Log ("DNS {0}: OK {1}" -f $Name, $addresses)
    } catch {
        Write-Log ("DNS {0}: FAIL {1}" -f $Name, $_.Exception.Message) 'WARN'
    }
}

try {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $LogPath = Join-Path $LogDir ('check-vpn-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Write-Log 'Проверка аварийного VPN.'

    $amneziawg = Find-AmneziaWGExecutable
    if ($amneziawg) {
        Write-Log ("AmneziaWG: найден {0}" -f $amneziawg)
    } else {
        Write-Log 'AmneziaWG: не найден' 'WARN'
    }

    if (Test-Path -LiteralPath $MarkerPath) {
        try {
            $marker = Get-Content -LiteralPath $MarkerPath -Raw | ConvertFrom-Json
            Write-Log ("Активный marker: tunnel={0}; profile={1}; endpoint={2}" -f $marker.TunnelName, $marker.SourceProfile, $marker.Endpoint)
        } catch {
            Write-Log ("Marker поврежден: {0}" -f $_.Exception.Message) 'WARN'
        }
    } else {
        Write-Log 'Активный marker не найден.'
    }

    $services = @(Get-EmergencyServices)
    if ($services.Count -eq 0) {
        Write-Log 'Аварийные tunnel services не найдены.' 'WARN'
    } else {
        foreach ($service in $services) {
            Write-Log ("Tunnel service {0}: {1}" -f $service.Name, $service.Status)
        }
    }

    $adapters = @(Get-NetAdapter -ErrorAction SilentlyContinue |
        Where-Object {
            $_.Name -match '(?i)(amnezia|wireguard|autostopvpn|emergency)' -or
            $_.InterfaceDescription -match '(?i)(amnezia|wireguard)'
        })
    foreach ($adapter in $adapters) {
        Write-Log ("Adapter {0}: {1}; {2}" -f $adapter.Name, $adapter.Status, $adapter.InterfaceDescription)
        $ipInterface = Get-NetIPInterface -InterfaceIndex $adapter.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($ipInterface) {
            Write-Log ("Adapter {0}: IPv4 MTU {1}" -f $adapter.Name, $ipInterface.NlMtu)
        }
    }

    Write-Log ("Endpoint профилей: {0} UDP. TCP-проверка ниже не доказывает UDP-доступность, но помогает увидеть базовую маршрутизацию." -f $Endpoint)
    try {
        $tcp = Test-NetConnection -ComputerName $EndpointHost -Port 443 -InformationLevel Quiet -WarningAction SilentlyContinue
        Write-Log ("TCP route check {0}:443 = {1}" -f $EndpointHost, $tcp)
    } catch {
        Write-Log ("TCP route check {0}:443 failed: {1}" -f $EndpointHost, $_.Exception.Message) 'WARN'
    }

    Invoke-DnsCheck -Name 'cloudflare.com'
    Invoke-DnsCheck -Name 'microsoft.com'

    $ipResponse = Invoke-HttpCheck -Name 'external-ip' -Uri 'https://api.ipify.org'
    if ($ipResponse -and $ipResponse.Content) {
        Write-Log ("Внешний IP: {0}" -f ($ipResponse.Content.Trim()))
    }

    Invoke-HttpCheck -Name 'cloudflare-trace' -Uri 'https://www.cloudflare.com/cdn-cgi/trace' | Out-Null
    Invoke-HttpCheck -Name 'microsoft' -Uri 'https://www.microsoft.com' | Out-Null

    Write-Log 'Проверка завершена.'
    exit 0
} catch {
    Write-Log $_.Exception.Message 'ERROR'
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
