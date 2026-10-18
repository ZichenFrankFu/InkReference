[CmdletBinding()]
param(
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName = 'InkReference',
    [switch]$Browser,
    [Alias('no-open')][switch]$NoOpen,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$DataDir,
    [switch]$LegacyVenv
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
if ($LegacyVenv -and $PSBoundParameters.ContainsKey('EnvironmentName')) {
    throw 'Choose -LegacyVenv or -EnvironmentName, not both.'
}
$parameters = @{ Browser = $Browser; NoOpen = $NoOpen; Port = $Port }
if (-not [string]::IsNullOrWhiteSpace($DataDir)) { $parameters.DataDir = $DataDir }
if ($LegacyVenv) { $parameters.Desktop = -not $Browser }
else { $parameters.EnvironmentName = $EnvironmentName }
& (Join-Path $PSScriptRoot 'start.ps1') @parameters
exit $LASTEXITCODE
