[CmdletBinding()]
param(
    [switch]$CurrentEnvironment,
    [string]$Python,
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName,
    [switch]$Browser,
    [Alias('no-open')][switch]$NoOpen,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$DataDir,
    [switch]$LegacyVenv,
    [switch]$Desktop
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
try {
    . (Join-Path $PSScriptRoot 'runtime.ps1')
    if ($Desktop -and $Browser) { throw 'Choose -Desktop or -Browser.' }
    if ($LegacyVenv -and ($CurrentEnvironment -or $Python -or $EnvironmentName)) { throw '-LegacyVenv is the default mode. Omit it when selecting another environment.' }
    $selection = @(Get-InkEnvironmentArguments -Python $Python -CurrentEnvironment:$CurrentEnvironment -EnvironmentName $EnvironmentName)
    $bootstrap = Resolve-InkPython -ProjectRoot $ProjectRoot -Python $Python -CurrentEnvironment:$CurrentEnvironment -EnvironmentName $EnvironmentName
    $arguments = @($bootstrap.Prefix) + @((Join-Path $PSScriptRoot 'manage.py'), 'open') + $selection + @('--port', [string]$Port)
    if ($Browser) { $arguments += '--browser' }
    if ($NoOpen) { $arguments += '--no-open' }
    if (-not [string]::IsNullOrWhiteSpace($DataDir)) { $arguments += @('--data-dir', $DataDir) }
    & $bootstrap.Executable @arguments
    exit $LASTEXITCODE
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
