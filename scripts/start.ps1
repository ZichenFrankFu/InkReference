[CmdletBinding()]
param(
    [switch]$CurrentEnvironment,
    [string]$Python,
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName,
    [switch]$Desktop,
    [switch]$Browser,
    [Alias('no-open')][switch]$NoOpen,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$DataDir,
    [switch]$LegacyVenv
)

# Compatibility entry: the default interface is now the native desktop window.
& (Join-Path $PSScriptRoot 'open.ps1') @PSBoundParameters
exit $LASTEXITCODE
