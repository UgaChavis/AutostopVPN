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
        [Parameter(Mandatory = $true)][string]$TargetScript,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$ShortcutPath
    )

    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $powershell = Join-Path $env:WINDIR "System32\WindowsPowerShell\v1.0\powershell.exe"
    $shortcut.TargetPath = $powershell
    $shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$TargetScript`""
    $shortcut.WorkingDirectory = $WorkingDirectory
    $shortcut.IconLocation = "$powershell,0"
    $shortcut.Description = "Autostop VPN dashboard launcher"
    $shortcut.Save()
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
New-DesktopShortcut -TargetScript (Join-Path $installRoot "start_autostopvpn.ps1") -WorkingDirectory $installRoot -ShortcutPath $shortcutPath

Write-Host "Autostop VPN installed to $installRoot"
Write-Host "Desktop shortcut created at $shortcutPath"
