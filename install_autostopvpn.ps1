param(
    [string]$InstallRoot = "$env:LOCALAPPDATA\AutostopVPN",
    [string]$SourceRoot = $PSScriptRoot
)

$ErrorActionPreference = "Stop"

function Get-FullPath {
    param([Parameter(Mandatory = $true)][string]$Path)
    return [System.IO.Path]::GetFullPath($Path)
}

function Copy-ProjectTree {
    param(
        [Parameter(Mandatory = $true)][string]$SourceRoot,
        [Parameter(Mandatory = $true)][string]$DestinationRoot
    )

    $excludedNames = @(".git", "__pycache__", ".pytest_cache")
    New-Item -ItemType Directory -Path $DestinationRoot -Force | Out-Null

    Get-ChildItem -LiteralPath $SourceRoot -Force | Where-Object { $_.Name -notin $excludedNames } | ForEach-Object {
        $targetPath = Join-Path $DestinationRoot $_.Name
        if ($_.PSIsContainer) {
            Copy-ProjectTree -SourceRoot $_.FullName -DestinationRoot $targetPath
        } else {
            Copy-Item -LiteralPath $_.FullName -Destination $targetPath -Force
        }
    }
}

function Backup-LocalInstallState {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot
    )

    if (-not (Test-Path -LiteralPath $InstallRoot)) {
        return ""
    }

    $preserveNames = @("logs", "secret-backups")
    $existing = @($preserveNames | Where-Object { Test-Path -LiteralPath (Join-Path $InstallRoot $_) })
    if (-not $existing) {
        return ""
    }

    $backupRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("AutostopVPN-install-preserve-" + (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType Directory -Path $backupRoot -Force | Out-Null
    foreach ($name in $existing) {
        Copy-Item -LiteralPath (Join-Path $InstallRoot $name) -Destination (Join-Path $backupRoot $name) -Recurse -Force
    }
    return $backupRoot
}

function Restore-LocalInstallState {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [string]$BackupRoot
    )

    if (-not $BackupRoot -or -not (Test-Path -LiteralPath $BackupRoot)) {
        return
    }

    Get-ChildItem -LiteralPath $BackupRoot -Force | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $InstallRoot $_.Name) -Recurse -Force
    }
}

function New-ShortcutIcon {
    param(
        [Parameter(Mandatory = $true)][string]$IconPath
    )

    Add-Type -AssemblyName System.Drawing

    $size = 256
    $bitmap = New-Object System.Drawing.Bitmap $size, $size, ([System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.Clear([System.Drawing.Color]::Transparent)

    try {
        $outerBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            (New-Object System.Drawing.Rectangle 0, 0, $size, $size),
            ([System.Drawing.Color]::FromArgb(255, 8, 20, 44)),
            ([System.Drawing.Color]::FromArgb(255, 12, 116, 146)),
            45
        )
        $innerBrush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
            (New-Object System.Drawing.Rectangle 38, 38, 180, 180),
            ([System.Drawing.Color]::FromArgb(255, 18, 168, 145)),
            ([System.Drawing.Color]::FromArgb(255, 14, 90, 120)),
            45
        )
        $ringPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(90, 255, 255, 255), 8)
        $outlinePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(220, 230, 248, 255), 6)
        $signalPen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(230, 245, 253, 255), 12)

        [System.Drawing.Point[]]$shieldPoints = @(
            [System.Drawing.Point]::new(128, 28),
            [System.Drawing.Point]::new(198, 52),
            [System.Drawing.Point]::new(188, 132),
            [System.Drawing.Point]::new(128, 220),
            [System.Drawing.Point]::new(68, 132),
            [System.Drawing.Point]::new(58, 52)
        )

        $graphics.FillEllipse($outerBrush, 8, 8, 240, 240)
        $graphics.DrawEllipse($ringPen, 14, 14, 228, 228)
        $graphics.FillPolygon($innerBrush, $shieldPoints)
        $graphics.DrawPolygon($outlinePen, $shieldPoints)
        $graphics.DrawArc($signalPen, 86, 56, 84, 56, 205, 130)
        $graphics.DrawArc($signalPen, 76, 74, 104, 72, 205, 130)

        $lockBodyBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 245, 250, 255))
        $keyholeBrush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(255, 19, 96, 124))
        $lockBodyRect = New-Object System.Drawing.Rectangle(94, 126, 68, 60)
        $graphics.FillRectangle($lockBodyBrush, $lockBodyRect)
        $graphics.DrawRectangle($outlinePen, $lockBodyRect)

        $shacklePen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(255, 245, 250, 255), 12)
        $graphics.DrawArc($shacklePen, 90, 78, 76, 64, 200, 140)
        $graphics.FillEllipse($keyholeBrush, 121, 148, 14, 18)
        $graphics.FillRectangle($keyholeBrush, 126, 160, 4, 16)

        $pngStream = New-Object System.IO.MemoryStream
        try {
            $bitmap.Save($pngStream, [System.Drawing.Imaging.ImageFormat]::Png)
            $pngBytes = $pngStream.ToArray()
        } finally {
            $pngStream.Dispose()
        }

        $fileStream = [System.IO.File]::Open($IconPath, [System.IO.FileMode]::Create)
        $writer = New-Object System.IO.BinaryWriter($fileStream)
        try {
            $writer.Write([uint16]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]1)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([byte]0)
            $writer.Write([uint16]1)
            $writer.Write([uint16]32)
            $writer.Write([int32]$pngBytes.Length)
            $writer.Write([int32]22)
            $writer.Write($pngBytes)
        } finally {
            $writer.Dispose()
            $fileStream.Dispose()
        }
    } finally {
        $graphics.Dispose()
        $bitmap.Dispose()
    }
}

function New-DesktopShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$TargetExecutable,
        [Parameter(Mandatory = $true)][string]$TargetArguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$ShortcutPath,
        [Parameter(Mandatory = $false)][string]$IconLocation = ""
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $TargetExecutable
    $shortcut.Arguments = $TargetArguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    if ($IconLocation) {
        $shortcut.IconLocation = $IconLocation
    } else {
        $shortcut.IconLocation = "$TargetExecutable,0"
    }
    $shortcut.Description = "Autostop VPN shell launcher"
    $shortcut.Save()
}

function Get-PythonExecutable {
    foreach ($name in @("pythonw.exe", "python.exe")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }
    throw "Python executable not found."
}

function Stop-RunningAutostopVpn {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot
    )

    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -in @("pythonw.exe", "python.exe", "ssh.exe") -and (
            $_.CommandLine -like "*$InstallRoot*" -or
            $_.CommandLine -like "*start_autostopvpn.ps1*" -or
            $_.CommandLine -like "*open_amnezia_dashboard.ps1*" -or
            $_.CommandLine -like "*127.0.0.1:18765*"
        )
    }

    foreach ($process in $processes) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
        } catch {
            Write-Warning "Could not stop process $($process.ProcessId): $($_.Exception.Message)"
        }
    }
}

function Wait-ForAutostopVpnShutdown {
    param(
        [Parameter(Mandatory = $true)][string]$InstallRoot,
        [int]$TimeoutSeconds = 12
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        $remaining = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $_.Name -in @("pythonw.exe", "python.exe", "ssh.exe") -and (
                $_.CommandLine -like "*$InstallRoot*" -or
                $_.CommandLine -like "*start_autostopvpn.ps1*" -or
                $_.CommandLine -like "*open_amnezia_dashboard.ps1*" -or
                $_.CommandLine -like "*127.0.0.1:18765*" -or
                $_.CommandLine -like "*AutostopVPN*"
            )
        }
        if (-not $remaining) {
            return
        }
        Start-Sleep -Milliseconds 500
    }
    throw "Timed out waiting for Autostop VPN processes to exit."
}

$sourceRoot = Get-FullPath $SourceRoot
$installRoot = Get-FullPath $InstallRoot
$expectedRoot = Get-FullPath (Join-Path $env:LOCALAPPDATA "AutostopVPN")
if ($installRoot -ne $expectedRoot) {
    throw "Install root must be $expectedRoot"
}
if ($sourceRoot -eq $installRoot) {
    throw "Run the installer from the source repository, not from the installed copy."
}

$preservedInstallState = Backup-LocalInstallState -InstallRoot $installRoot
if (Test-Path $installRoot) {
    Stop-RunningAutostopVpn -InstallRoot $installRoot
    Wait-ForAutostopVpnShutdown -InstallRoot $installRoot
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Remove-Item -LiteralPath $installRoot -Recurse -Force
            break
        } catch {
            if ($attempt -eq 5) {
                throw
            }
            Start-Sleep -Seconds $attempt
        }
    }
}
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
Copy-ProjectTree -SourceRoot $sourceRoot -DestinationRoot $installRoot
Restore-LocalInstallState -InstallRoot $installRoot -BackupRoot $preservedInstallState

    $desktop = [Environment]::GetFolderPath("Desktop")
    $shortcutPath = Join-Path $desktop "Autostop VPN.lnk"
    $iconPath = Join-Path $installRoot "AutostopVPN.ico"
    $powershell = (Get-Command powershell.exe).Source
    $launcherScript = Join-Path $installRoot "start_autostopvpn.ps1"
    $arguments = @(
        "-NoProfile",
        "-WindowStyle", "Hidden",
        "-ExecutionPolicy", "Bypass",
        "-File", "`"$launcherScript`""
    ) -join " "
    try {
        New-ShortcutIcon -IconPath $iconPath
    } catch {
        Write-Warning "Could not generate custom icon: $($_.Exception.Message)"
    }
    New-DesktopShortcut -TargetExecutable $powershell -TargetArguments $arguments -WorkingDirectory $installRoot -ShortcutPath $shortcutPath -IconLocation "$iconPath,0"

    Write-Host "Autostop VPN installed to $installRoot"
    Write-Host "Desktop shortcut created at $shortcutPath"
