[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$StartScript = Join-Path $PSScriptRoot 'Start-CAD-AI.ps1'
if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
    throw "Start script not found: $StartScript"
}

$desktop = [Environment]::GetFolderPath('Desktop')
$shortcutPath = Join-Path $desktop 'CAD AI Assistant.lnk'
$shell = New-Object -ComObject WScript.Shell
$expectedArguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`""

if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
    $existing = $shell.CreateShortcut($shortcutPath)
    if ($existing.Arguments -eq $expectedArguments) {
        Write-Output "Shortcut already correct: $shortcutPath"
        exit 0
    }
    throw "A different shortcut already exists and was not overwritten: $shortcutPath"
}

$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"
$shortcut.Arguments = $expectedArguments
$shortcut.WorkingDirectory = $ProjectRoot
$shortcut.Description = 'Start AutoCAD 2022, verify the enhanced MCP, and start Codex'
$shortcut.WindowStyle = 1
$shortcut.IconLocation = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe,0"
$shortcut.Save()
Write-Output "Shortcut created: $shortcutPath"
