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

function Get-TunnelNameFromService {
    param([Parameter(Mandatory = $true)]$Service)

    if ($Service.Name -match '^[^$]+\$(.+)$') {
        return $Matches[1]
    }
    return $Service.Name
}

function Invoke-UninstallTunnel {
    param(
        [AllowNull()][string]$Executable,
        [Parameter(Mandatory = $true)][string]$TunnelName,
        [Parameter(Mandatory = $true)][string]$ServiceName
    )

    if ($Executable) {
        Write-Log ("Удаляю tunnel service через amneziawg: {0}" -f $TunnelName)
        $output = & $Executable /uninstalltunnelservice $TunnelName 2>&1
        $exitCode = $LASTEXITCODE
        foreach ($line in $output) {
            if ($line) {
                Write-Log ("amneziawg: {0}" -f $line)
            }
        }
        if ($exitCode -eq 0) {
            return
        }
        Write-Log ("amneziawg uninstall вернул код {0}, пробую sc.exe delete." -f $exitCode) 'WARN'
    } else {
        Write-Log 'amneziawg.exe не найден, пробую удалить service через sc.exe.' 'WARN'
    }

    $null = & sc.exe delete $ServiceName 2>&1
}

try {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $LogPath = Join-Path $LogDir ('cleanup-this-pc-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Write-Log 'Очистка этого ПК от аварийного VPN.'

    if (-not (Test-IsAdministrator)) {
        Restart-SelfElevated
    }

    $amneziawg = Find-AmneziaWGExecutable
    $services = @(Get-EmergencyServices)
    foreach ($service in $services) {
        if ($service.Status -ne 'Stopped') {
            Write-Log ("Останавливаю: {0}" -f $service.Name)
            Stop-Service -Name $service.Name -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }

        $tunnelName = Get-TunnelNameFromService -Service $service
        Invoke-UninstallTunnel -Executable $amneziawg -TunnelName $tunnelName -ServiceName $service.Name
    }

    if (Test-Path -LiteralPath $ProgramDataRoot) {
        Get-ChildItem -LiteralPath $ProgramDataRoot -Filter '*.conf' -File -Recurse -ErrorAction SilentlyContinue |
            Remove-Item -Force -ErrorAction SilentlyContinue
        if (Test-Path -LiteralPath $MarkerPath) {
            Remove-Item -LiteralPath $MarkerPath -Force -ErrorAction SilentlyContinue
        }
        Get-ChildItem -LiteralPath $ProgramDataRoot -Directory -Recurse -ErrorAction SilentlyContinue |
            Sort-Object FullName -Descending |
            ForEach-Object {
                if (-not (Get-ChildItem -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue)) {
                    Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
                }
            }
        if (-not (Get-ChildItem -LiteralPath $ProgramDataRoot -Force -ErrorAction SilentlyContinue)) {
            Remove-Item -LiteralPath $ProgramDataRoot -Force -ErrorAction SilentlyContinue
        }
    }

    $tempRoot = Join-Path $env:TEMP 'AutostopVPN-Emergency'
    if (Test-Path -LiteralPath $tempRoot) {
        Remove-Item -LiteralPath $tempRoot -Recurse -Force -ErrorAction SilentlyContinue
    }

    Write-Log 'Очистка завершена. Профили на флешке не удалялись.'
    exit 0
} catch {
    Write-Log $_.Exception.Message 'ERROR'
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
