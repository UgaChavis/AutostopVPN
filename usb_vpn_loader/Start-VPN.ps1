[CmdletBinding()]
param(
    [string]$ProfileName
)

$ErrorActionPreference = 'Stop'

$BaseDir = Split-Path -Parent $PSCommandPath
$ProfilesDir = Join-Path $BaseDir 'profiles'
$InstallersDir = Join-Path $BaseDir 'installers'
$LogDir = Join-Path $BaseDir 'logs'
$ProgramDataRoot = Join-Path $env:ProgramData 'AutostopVPN\Emergency'
$ProgramDataProfiles = Join-Path $ProgramDataRoot 'profiles'
$MarkerPath = Join-Path $ProgramDataRoot 'active-tunnel.json'
$Endpoint = '46.8.254.243:443'
$Mtu = '1280'
$Keepalive = '25'
$LogPath = $null

function Protect-LogText {
    param([AllowNull()][object]$Value)

    $text = [string]$Value
    $text = $text -replace '(?im)(PrivateKey\s*=\s*)[^\r\n;#]+', '$1<hidden>'
    $text = $text -replace '(?im)(PresharedKey\s*=\s*)[^\r\n;#]+', '$1<hidden>'
    return $text
}

function Write-Log {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [string]$Level = 'INFO'
    )

    $line = '{0} [{1}] {2}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Level, (Protect-LogText $Message)
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
    $arguments = @(
        '-NoProfile',
        '-ExecutionPolicy',
        'Bypass',
        '-File',
        ('"{0}"' -f $PSCommandPath)
    )
    if ($ProfileName) {
        $arguments += '-ProfileName'
        $arguments += ('"{0}"' -f $ProfileName)
    }

    Write-Log 'Запрашиваю права администратора через UAC.'
    Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WorkingDirectory $BaseDir | Out-Null
    exit 0
}

function Get-ExpectedProfileNames {
    for ($index = 1; $index -le 5; $index++) {
        'usb-emergency-{0:D2}.conf' -f $index
    }
}

function Resolve-RequestedProfileName {
    param([AllowNull()][string]$RequestedName)

    if ([string]::IsNullOrWhiteSpace($RequestedName)) {
        return $null
    }

    $value = $RequestedName.Trim()
    if ($value -match '^\d$') {
        return 'usb-emergency-0{0}.conf' -f $value
    }
    if ($value -match '^\d\d$') {
        return 'usb-emergency-{0}.conf' -f $value
    }
    if ($value -match '^usb-emergency-\d\d$') {
        return "$value.conf"
    }
    return $value
}

function Test-ProfileConfigured {
    param([Parameter(Mandatory = $true)][string]$Path)

    $reasons = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $Path)) {
        $reasons.Add('файл отсутствует')
        return [pscustomobject]@{ Ready = $false; Reasons = $reasons.ToArray() }
    }

    $text = Get-Content -LiteralPath $Path -Raw
    if ([string]::IsNullOrWhiteSpace($text)) {
        $reasons.Add('файл пустой')
    }
    if ($text -match '(?i)PLACEHOLDER|REPLACE|TODO|<client private key>|<server public key>') {
        $reasons.Add('это шаблон, нужен реальный профиль')
    }
    if ($text -notmatch '(?im)^\s*PrivateKey\s*=\s*\S+') {
        $reasons.Add('нет PrivateKey')
    }
    if ($text -notmatch '(?im)^\s*Endpoint\s*=\s*46\.8\.254\.243:443(?:\s*(?:[#;].*)?)?$') {
        $reasons.Add("Endpoint должен быть $Endpoint")
    }
    if ($text -notmatch '(?im)^\s*MTU\s*=\s*1280(?:\s*(?:[#;].*)?)?$') {
        $reasons.Add("MTU должен быть $Mtu")
    }
    if ($text -notmatch '(?im)^\s*PersistentKeepalive\s*=\s*25(?:\s*(?:[#;].*)?)?$') {
        $reasons.Add("PersistentKeepalive должен быть $Keepalive")
    }

    return [pscustomobject]@{ Ready = ($reasons.Count -eq 0); Reasons = $reasons.ToArray() }
}

function Get-ProfileSlots {
    $slots = @()
    $index = 0
    foreach ($name in Get-ExpectedProfileNames) {
        $index++
        $path = Join-Path $ProfilesDir $name
        $check = Test-ProfileConfigured -Path $path
        $status = 'готов'
        if (-not $check.Ready) {
            $status = ($check.Reasons -join '; ')
        }

        $slots += [pscustomobject]@{
            Index = $index
            Name = $name
            Path = $path
            Ready = $check.Ready
            Status = $status
        }
    }
    return $slots
}

function Select-Profile {
    $requestedName = Resolve-RequestedProfileName -RequestedName $ProfileName
    $slots = Get-ProfileSlots

    if ($requestedName) {
        $requested = $slots | Where-Object { $_.Name -ieq $requestedName } | Select-Object -First 1
        if (-not $requested) {
            throw "Неизвестный профиль: $requestedName. Используйте usb-emergency-01.conf ... usb-emergency-05.conf."
        }
        if (-not $requested.Ready) {
            throw "Профиль $($requested.Name) не готов: $($requested.Status)"
        }
        return $requested
    }

    Write-Host ''
    Write-Host 'Доступные аварийные профили:'
    foreach ($slot in $slots) {
        Write-Host ('  {0}. {1} - {2}' -f $slot.Index, $slot.Name, $slot.Status)
    }
    Write-Host ''

    while ($true) {
        $choice = Read-Host 'Выберите профиль 1-5'
        if ($choice -match '^[1-5]$') {
            $slot = $slots[[int]$choice - 1]
            if (-not $slot.Ready) {
                Write-Host ("Профиль {0} не готов: {1}" -f $slot.Name, $slot.Status) -ForegroundColor Yellow
                continue
            }
            return $slot
        }
        Write-Host 'Введите число от 1 до 5.' -ForegroundColor Yellow
    }
}

function Find-AmneziaWGExecutable {
    foreach ($commandName in @('amneziawg.exe', 'amneziawg')) {
        $command = Get-Command $commandName -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) {
            return $command.Source
        }
    }

    $candidates = @()
    if ($env:ProgramFiles) {
        $candidates += (Join-Path $env:ProgramFiles 'AmneziaWG\amneziawg.exe')
        $candidates += (Join-Path $env:ProgramFiles 'AmneziaVPN\amneziawg.exe')
        $candidates += (Join-Path $env:ProgramFiles 'AmneziaVPN\AmneziaWG\amneziawg.exe')
    }
    $programFilesX86 = ${env:ProgramFiles(x86)}
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

    foreach ($root in @($env:ProgramFiles, $programFilesX86)) {
        if (-not $root -or -not (Test-Path -LiteralPath $root)) {
            continue
        }
        $amneziaDirs = Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -like 'Amnezia*' }
        foreach ($dir in $amneziaDirs) {
            $match = Get-ChildItem -LiteralPath $dir.FullName -Filter 'amneziawg.exe' -File -Recurse -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if ($match) {
                return $match.FullName
            }
        }
    }

    return $null
}

function Install-AmneziaWGClient {
    if (-not (Test-Path -LiteralPath $InstallersDir)) {
        throw "Папка installers не найдена: $InstallersDir"
    }

    $installer = Get-ChildItem -LiteralPath $InstallersDir -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Extension -in @('.msi', '.exe') } |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1

    if (-not $installer) {
        throw "AmneziaWG не найден. Положите установщик .msi или .exe в $InstallersDir и запустите снова."
    }

    Write-Log ("AmneziaWG не найден, запускаю установщик из installers: {0}" -f $installer.Name)
    if ($installer.Extension -ieq '.msi') {
        $args = @('/i', ('"{0}"' -f $installer.FullName), '/passive', '/norestart')
        $process = Start-Process -FilePath 'msiexec.exe' -ArgumentList $args -Wait -PassThru
    } else {
        $process = Start-Process -FilePath $installer.FullName -Wait -PassThru
    }

    if ($process.ExitCode -ne 0) {
        Write-Log ("Установщик вернул код {0}. Проверяю наличие клиента." -f $process.ExitCode) 'WARN'
    }

    for ($attempt = 1; $attempt -le 20; $attempt++) {
        $exe = Find-AmneziaWGExecutable
        if ($exe) {
            Write-Log ("AmneziaWG найден после установки: {0}" -f $exe)
            return $exe
        }
        Start-Sleep -Seconds 3
    }

    throw 'Установка завершилась, но amneziawg.exe не найден. Установите AmneziaWG вручную и запустите снова.'
}

function Get-EmergencyServices {
    Get-Service -ErrorAction SilentlyContinue |
        Where-Object {
            $text = '{0} {1}' -f $_.Name, $_.DisplayName
            $text -match '(?i)(autostopvpn-emergency|usb-emergency)'
        }
}

function Get-TunnelServicesByName {
    param([Parameter(Mandatory = $true)][string]$TunnelName)

    $serviceNames = @(
        ("AmneziaWGTunnel`$" + $TunnelName),
        ("WireGuardTunnel`$" + $TunnelName)
    )

    Get-Service -ErrorAction SilentlyContinue |
        Where-Object { $serviceNames -contains $_.Name -or $_.Name -like "*$TunnelName*" }
}

function Invoke-AmneziaWG {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )

    $output = & $Executable @Arguments 2>&1
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        if ($line) {
            Write-Log ("amneziawg: {0}" -f $line)
        }
    }
    if ($exitCode -ne 0) {
        throw "amneziawg завершился с кодом $exitCode"
    }
}

function Stop-OtherEmergencyServices {
    param([Parameter(Mandatory = $true)][string]$ExceptTunnelName)

    foreach ($service in Get-EmergencyServices) {
        if ($service.Name -like "*$ExceptTunnelName*") {
            continue
        }
        if ($service.Status -ne 'Stopped') {
            Write-Log ("Останавливаю другой аварийный tunnel service: {0}" -f $service.Name)
            Stop-Service -Name $service.Name -Force -ErrorAction SilentlyContinue
        }
    }
}

function Remove-ExistingTunnelService {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string]$TunnelName
    )

    $services = @(Get-TunnelServicesByName -TunnelName $TunnelName)
    if ($services.Count -eq 0) {
        return
    }

    foreach ($service in $services) {
        if ($service.Status -ne 'Stopped') {
            Write-Log ("Останавливаю существующий tunnel service: {0}" -f $service.Name)
            Stop-Service -Name $service.Name -Force -ErrorAction SilentlyContinue
            Start-Sleep -Seconds 1
        }
    }

    Write-Log ("Удаляю существующий tunnel service для обновления профиля: {0}" -f $TunnelName)
    Invoke-AmneziaWG -Executable $Executable -Arguments @('/uninstalltunnelservice', $TunnelName)
}

try {
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $LogPath = Join-Path $LogDir ('start-vpn-{0}.log' -f (Get-Date -Format 'yyyyMMdd-HHmmss'))
    Write-Log 'Запуск аварийного USB VPN loader.'

    if (-not (Test-IsAdministrator)) {
        Restart-SelfElevated
    }

    New-Item -ItemType Directory -Path $ProgramDataProfiles -Force | Out-Null

    $selectedProfile = Select-Profile
    Write-Log ("Выбран профиль: {0}" -f $selectedProfile.Name)

    $amneziawg = Find-AmneziaWGExecutable
    if (-not $amneziawg) {
        $amneziawg = Install-AmneziaWGClient
    } else {
        Write-Log ("AmneziaWG найден: {0}" -f $amneziawg)
    }

    $suffix = $selectedProfile.Name -replace '^usb-emergency-', '' -replace '\.conf$', ''
    $tunnelName = 'autostopvpn-emergency-{0}' -f $suffix
    $localConfigPath = Join-Path $ProgramDataProfiles ("$tunnelName.conf")

    Stop-OtherEmergencyServices -ExceptTunnelName $tunnelName
    Remove-ExistingTunnelService -Executable $amneziawg -TunnelName $tunnelName

    Copy-Item -LiteralPath $selectedProfile.Path -Destination $localConfigPath -Force
    Write-Log ("Временный профиль скопирован на ПК: {0}" -f $localConfigPath)

    Write-Log ("Устанавливаю tunnel service: {0}" -f $tunnelName)
    Invoke-AmneziaWG -Executable $amneziawg -Arguments @('/installtunnelservice', $localConfigPath)

    Start-Sleep -Seconds 2
    $services = @(Get-TunnelServicesByName -TunnelName $tunnelName)
    foreach ($service in $services) {
        if ($service.Status -ne 'Running') {
            Write-Log ("Запускаю service: {0}" -f $service.Name)
            Start-Service -Name $service.Name -ErrorAction Stop
        }
    }

    $runningServices = @(Get-TunnelServicesByName -TunnelName $tunnelName)
    if ($runningServices.Count -eq 0) {
        throw "Tunnel service не найден после установки: $tunnelName"
    }

    foreach ($service in $runningServices) {
        Write-Log ("Статус service {0}: {1}" -f $service.Name, $service.Status)
    }

    $marker = [pscustomobject]@{
        TunnelName = $tunnelName
        SourceProfile = $selectedProfile.Name
        ConfigPath = $localConfigPath
        ServiceNames = @($runningServices | ForEach-Object { $_.Name })
        Endpoint = $Endpoint
        Mtu = $Mtu
        PersistentKeepalive = $Keepalive
        StartedAt = (Get-Date).ToString('s')
    }
    $marker | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $MarkerPath -Encoding UTF8

    Write-Log 'VPN запущен.'
    Write-Host ''
    Write-Host 'VPN запущен. Для проверки запустите Check-VPN.ps1.' -ForegroundColor Green
    exit 0
} catch {
    Write-Log $_.Exception.Message 'ERROR'
    Write-Host ''
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
