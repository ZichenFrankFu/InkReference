[CmdletBinding()]
param(
    [switch]$CurrentEnvironment,
    [string]$Python,
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName,
    [switch]$BrowserOnly,
    [switch]$MinerU,
    [switch]$Dev,
    [switch]$LegacyVenv
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
try {
    . (Join-Path $PSScriptRoot 'runtime.ps1')
    if ($LegacyVenv -and ($CurrentEnvironment -or $Python -or $EnvironmentName)) { throw '-LegacyVenv is the default mode. Omit it when selecting another environment.' }
    $selection = @(Get-InkEnvironmentArguments -Python $Python -CurrentEnvironment:$CurrentEnvironment -EnvironmentName $EnvironmentName)
    $bootstrap = Resolve-InkPython -ProjectRoot $ProjectRoot -Python $Python -CurrentEnvironment:$CurrentEnvironment -EnvironmentName $EnvironmentName
    $arguments = @($bootstrap.Prefix) + @((Join-Path $PSScriptRoot 'manage.py'), 'install') + $selection
    if ($BrowserOnly) { $arguments += '--browser-only' }
    if ($MinerU) { $arguments += '--mineru' }
    if ($Dev) { $arguments += '--dev' }
    & $bootstrap.Executable @arguments
    exit $LASTEXITCODE
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}
