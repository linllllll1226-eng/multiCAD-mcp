[CmdletBinding()]
param(
    [ValidateRange(15, 300)]
    [int]$ComTimeoutSeconds = 120,
    [switch]$NoLaunchCodex
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$MemoryDb = Join-Path $ProjectRoot 'data\cad_memory.db'
$CodexConfig = Join-Path $env:USERPROFILE '.codex\config.toml'

function Write-State {
    param([string]$Name, [bool]$Passed, [string]$Detail)
    $label = if ($Passed) { 'OK' } else { 'FAIL' }
    $color = if ($Passed) { 'Green' } else { 'Red' }
    Write-Host ("[{0}] {1}: {2}" -f $label, $Name, $Detail) -ForegroundColor $color
}

function Get-AutoCADComObject {
    try {
        return [Runtime.InteropServices.Marshal]::GetActiveObject('AutoCAD.Application')
    }
    catch {
        return $null
    }
}

function Find-AutoCAD2022Executable {
    $candidates = @(
        'C:\Program Files\Autodesk\AutoCAD 2022\acad.exe',
        'D:\Program Files\Autodesk\AutoCAD 2022\acad.exe'
    )
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return $candidate
        }
    }
    $command = Get-Command 'acad.exe' -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    foreach ($root in @(
        'HKLM:\SOFTWARE\Autodesk\AutoCAD\R24.1',
        'HKLM:\SOFTWARE\WOW6432Node\Autodesk\AutoCAD\R24.1'
    )) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        foreach ($key in Get-ChildItem -LiteralPath $root -Recurse -ErrorAction SilentlyContinue) {
            $values = Get-ItemProperty -LiteralPath $key.PSPath -ErrorAction SilentlyContinue
            foreach ($property in @('AcadLocation', 'InstallLocation')) {
                $folder = $values.$property
                if ($folder) {
                    $candidate = Join-Path $folder 'acad.exe'
                    if (Test-Path -LiteralPath $candidate -PathType Leaf) {
                        return $candidate
                    }
                }
            }
        }
    }
    return $null
}

Write-Host 'CAD AI Assistant preflight' -ForegroundColor Cyan
Write-Host 'This script reads status only; it does not open or modify a DWG.'

$acad = Get-AutoCADComObject
if (-not $acad) {
    $acadProcess = Get-Process -Name 'acad' -ErrorAction SilentlyContinue
    if (-not $acadProcess) {
        $acadExe = Find-AutoCAD2022Executable
        if (-not $acadExe) {
            Write-State 'AutoCAD 2022' $false 'acad.exe was not found'
            exit 1
        }
        Write-Host "Starting AutoCAD 2022: $acadExe"
        Start-Process -FilePath $acadExe | Out-Null
    }
    else {
        Write-Host 'AutoCAD process exists; waiting for COM initialization.'
    }

    $deadline = [DateTime]::UtcNow.AddSeconds($ComTimeoutSeconds)
    while (-not $acad -and [DateTime]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 1
        $acad = Get-AutoCADComObject
    }
}

if (-not $acad) {
    Write-State 'AutoCAD COM' $false "not available after $ComTimeoutSeconds seconds"
    exit 1
}

$version = [string]$acad.Version
$versionPassed = $version.StartsWith('24.1')
$documentName = '<no active document>'
try { $documentName = [string]$acad.ActiveDocument.Name } catch { }
Write-State 'AutoCAD COM' $versionPassed "version=$version; active=$documentName"

$configPassed = $false
$configDetail = 'config.toml is missing'
if (Test-Path -LiteralPath $CodexConfig -PathType Leaf) {
    $configText = Get-Content -LiteralPath $CodexConfig -Raw
    $section = [regex]::Match(
        $configText,
        '(?ms)^\[mcp_servers\.autocad\]\s*.*?(?=^\[|\z)'
    )
    if ($section.Success -and $section.Value -match 'server_memory\.py') {
        $configPassed = $true
        $configDetail = "autocad points to server_memory.py ($CodexConfig)"
    }
    else {
        $configDetail = "autocad does not point to server_memory.py ($CodexConfig)"
    }
}
Write-State 'Codex MCP config' $configPassed $configDetail

$dbPassed = $false
$dbDetail = 'cad_memory.db is missing'
if ((Test-Path -LiteralPath $MemoryDb -PathType Leaf) -and (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    $sqliteCheck = & $PythonExe -c "import sqlite3,sys; p=sys.argv[1]; c=sqlite3.connect('file:' + p.replace('\\','/') + '?mode=ro', uri=True); print(c.execute('PRAGMA quick_check').fetchone()[0]); c.close()" $MemoryDb 2>$null
    if ($LASTEXITCODE -eq 0 -and ($sqliteCheck | Select-Object -Last 1) -eq 'ok') {
        $dbPassed = $true
        $dbDetail = "read-only SQLite quick_check=ok ($MemoryDb)"
    }
    else {
        $dbDetail = "SQLite read-only check failed ($MemoryDb)"
    }
}
Write-State 'CAD memory database' $dbPassed $dbDetail

if (-not ($versionPassed -and $configPassed -and $dbPassed)) {
    Write-Host 'Codex was not started because one or more preflight checks failed.' -ForegroundColor Yellow
    exit 1
}

if ($NoLaunchCodex) {
    Write-Host 'Codex launch skipped by -NoLaunchCodex.'
    exit 0
}

if (Get-Process -Name 'Codex' -ErrorAction SilentlyContinue) {
    Write-State 'Codex' $true 'desktop process is already running'
    exit 0
}

$codexCommands = @(Get-Command 'codex' -All -ErrorAction SilentlyContinue)
$desktopCodex = $codexCommands | Where-Object {
    $_.Source -like '*\WindowsApps\OpenAI.Codex_*\app\resources\codex.exe'
} | Select-Object -First 1
if ($desktopCodex) {
    Start-Process -FilePath $desktopCodex.Source -WorkingDirectory $ProjectRoot | Out-Null
    Write-State 'Codex' $true "desktop app started from $($desktopCodex.Source)"
    exit 0
}

$codex = $codexCommands | Select-Object -First 1
if (-not $codex) {
    Write-State 'Codex' $false 'Codex Desktop and codex CLI were not found'
    exit 1
}
if ($codex.Source -match '\.(ps1|cmd)$' -and -not (Get-Command 'node.exe' -ErrorAction SilentlyContinue)) {
    Write-State 'Codex' $false "CLI wrapper requires node.exe: $($codex.Source)"
    exit 1
}
$escapedCodex = $codex.Source.Replace("'", "''")
$codexCommand = "& '$escapedCodex'"
Start-Process -FilePath 'powershell.exe' -WorkingDirectory $ProjectRoot -ArgumentList @(
    '-NoExit',
    '-NoProfile',
    '-Command',
    $codexCommand
) | Out-Null
Write-State 'Codex' $true "CLI started from $($codex.Source)"
