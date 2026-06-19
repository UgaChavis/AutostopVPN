[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

$BaseDir = Split-Path -Parent $PSCommandPath
$LogDir = Join-Path $BaseDir 'logs'
$ProgramDataRoot = Join-Path $env:ProgramData 'AutostopVPN\Emergency'
$MarkerPath = Join-Path $ProgramDataRoot 'active-tunnel.json'
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

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Restart-SelfElevated {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath))
    Write-Log 'Запрашиваю права администратора через UAC.'
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WorkingDirectory $BaseDir | Out-Null
    exit 0
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

try {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $LogPath = Join-Path $LogDir ('stop-vpn-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Write-Log 'Остановка аварийного VPN.'

    if (-not (Test-IsAdministrator)) {
        Restart-SelfElevated
    }

    $services = @(Get-EmergencyServices)
    if ($services.Count -eq 0) {
        Write-Log 'Аварийные tunnel services не найдены.'
        exit 0
    }

    foreach ($service in $services) {
        if ($service.Status -eq 'Stopped') {
            Write-Log ("Уже остановлен: {0}" -f $service.Name)
            continue
        }
        Write-Log ("Останавливаю: {0}" -f $service.Name)
        Stop-Service -Name $service.Name -Force -ErrorAction Stop
    }

    Write-Log 'VPN отключен.'
    exit 0
} catch {
    Write-Log $_.Exception.Message 'ERROR'
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
