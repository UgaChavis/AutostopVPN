param(
    [switch]$RunTests
)

$ErrorActionPreference = "Stop"

function Write-Section {
    param([Parameter(Mandatory = $true)][string]$Title)

    Write-Host ""
    Write-Host "== $Title =="
}

function Invoke-OptionalNative {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowNoMatches
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $nativeCommandPreference = Get-Variable -Name PSNativeCommandUseErrorActionPreference -ErrorAction SilentlyContinue
    $previousNativeCommandPreference = $null
    try {
        $ErrorActionPreference = "Continue"
        if ($nativeCommandPreference) {
            $previousNativeCommandPreference = $PSNativeCommandUseErrorActionPreference
            $PSNativeCommandUseErrorActionPreference = $false
        }
        & $FilePath @Arguments
        $exitCode = $LASTEXITCODE
    } finally {
        if ($nativeCommandPreference) {
            $PSNativeCommandUseErrorActionPreference = $previousNativeCommandPreference
        }
        $ErrorActionPreference = $previousErrorActionPreference
    }

    if ($exitCode -ne 0 -and -not ($AllowNoMatches -and $exitCode -eq 1)) {
        throw "$FilePath $($Arguments -join ' ') failed with exit code $exitCode"
    }
}

function Test-CommandAvailable {
    param([Parameter(Mandatory = $true)][string]$Name)

    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Set-Location $PSScriptRoot

Write-Host "AutostopVPN maintenance audit"
Write-Host "workspace=$PSScriptRoot"
Write-Host "run_tests=$($RunTests.IsPresent)"

Write-Section "Git status"
Invoke-OptionalNative -FilePath "git" -Arguments @("status", "--short", "--branch")

Write-Section "Tracked files by size"
$trackedFiles = & git ls-files
$trackedFiles |
    ForEach-Object {
        $path = $_
        $lineCount = (Get-Content -LiteralPath $path -ErrorAction SilentlyContinue | Measure-Object -Line).Lines
        [pscustomobject]@{ Lines = $lineCount; Path = $path }
    } |
    Sort-Object Lines -Descending |
    Format-Table -AutoSize

if (Test-CommandAvailable -Name "python") {
    Write-Section "Largest Python symbols"
    $pythonScript = @'
import ast
from pathlib import Path

for path in sorted(Path(".").glob("*.py")):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    rows = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            end = getattr(node, "end_lineno", node.lineno)
            rows.append((end - node.lineno + 1, type(node).__name__, node.name, node.lineno))
    print(f"## {path}")
    for length, kind, name, line in sorted(rows, reverse=True)[:12]:
        print(f"{length:4} lines {kind:12} {name:35} line {line}")
'@
    $pythonScript | python -
}

if (Test-CommandAvailable -Name "rg") {
    Write-Section "Stale or cleanup markers"
    Invoke-OptionalNative -FilePath "rg" -Arguments @(
        "-n",
        "\b(TODO|FIXME|XXX|HACK|deprecated|obsolete|temporary)\b|устар|мусор|временно|старый",
        "-g",
        "!audit_autostopvpn.ps1"
    ) -AllowNoMatches

    Write-Section "Secret-shaped markers"
    Invoke-OptionalNative -FilePath "rg" -Arguments @(
        "-n",
        "BEGIN OPENSSH|sk-[A-Za-z0-9]|api[_-]?key\s*[:=]|password\s*[:=]|token\s*[:=]|парол\s*[:=]",
        "-g",
        "!audit_autostopvpn.ps1"
    ) -AllowNoMatches

    Write-Section "Hard-coded user paths in docs"
    Invoke-OptionalNative -FilePath "rg" -Arguments @(
        "-n",
        "C:\\Users\\User|C:\\Users\\9860606",
        "-g",
        "*.md"
    ) -AllowNoMatches
}

Write-Section "Ignored runtime artifacts"
$artifactNames = @("__pycache__", ".pytest_cache", "data")
foreach ($name in $artifactNames) {
    Get-ChildItem -LiteralPath $PSScriptRoot -Force -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -eq $name } |
        ForEach-Object { $_.FullName }
}

if ($RunTests) {
    Write-Section "Unit tests"
    Invoke-OptionalNative -FilePath "python" -Arguments @("-m", "unittest", "discover", "-s", "tests", "-v")
}
