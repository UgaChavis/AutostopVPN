param(
    [string]$HostName = "46.8.254.189",
    [string]$SshUser = "root",
    [string]$KeyPath = "",
    [int]$LocalPort = 18765,
    [int]$RemotePort = 18080,
    [double]$RefreshSeconds = 1.0
)

$ErrorActionPreference = "Stop"

function Resolve-PythonExecutable {
    foreach ($candidate in @("pythonw.exe", "python.exe")) {
        $command = Get-Command $candidate -ErrorAction SilentlyContinue
        if ($command) {
            return $command.Source
        }
    }

    throw "Python executable not found. Install Python with tkinter support."
}

$pythonPath = Resolve-PythonExecutable
$shellScript = Join-Path $PSScriptRoot "amnezia_vpn_shell.py"
if (-not (Test-Path $shellScript)) {
    throw "Shell app not found: $shellScript"
}

$args = @(
    $shellScript,
    "--host", $HostName,
    "--ssh-user", $SshUser,
    "--local-port", "$LocalPort",
    "--remote-port", "$RemotePort",
    "--refresh-seconds", "$RefreshSeconds"
)
if ($KeyPath) {
    $args += @("--key-path", $KeyPath)
}

Start-Process -FilePath $pythonPath -ArgumentList $args -WorkingDirectory $PSScriptRoot | Out-Null
