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

function New-DesktopShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$TargetExecutable,
        [Parameter(Mandatory = $true)][string]$TargetArguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$ShortcutPath
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $TargetExecutable
    $shortcut.Arguments = $TargetArguments
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = "$TargetExecutable,0"
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

$sourceRoot = Get-FullPath $SourceRoot
$installRoot = Get-FullPath $InstallRoot
$expectedRoot = Get-FullPath (Join-Path $env:LOCALAPPDATA "AutostopVPN")
if ($installRoot -ne $expectedRoot) {
    throw "Install root must be $expectedRoot"
}
if ($sourceRoot -eq $installRoot) {
    throw "Run the installer from the source repository, not from the installed copy."
}

if (Test-Path $installRoot) {
    Remove-Item -LiteralPath $installRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
Copy-ProjectTree -SourceRoot $sourceRoot -DestinationRoot $installRoot

$desktop = [Environment]::GetFolderPath("Desktop")
$shortcutPath = Join-Path $desktop "Autostop VPN.lnk"
$python = Get-PythonExecutable
$shellScript = Join-Path $installRoot "amnezia_vpn_shell.py"
$arguments = "`"$shellScript`" --host 46.8.254.243 --ssh-user root --local-port 18765 --remote-port 18080 --refresh-seconds 5"
New-DesktopShortcut -TargetExecutable $python -TargetArguments $arguments -WorkingDirectory $installRoot -ShortcutPath $shortcutPath

Write-Host "Autostop VPN installed to $installRoot"
Write-Host "Desktop shortcut created at $shortcutPath"
