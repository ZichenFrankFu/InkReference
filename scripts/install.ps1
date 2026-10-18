[CmdletBinding()]
param(
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName = 'InkReference',
    [switch]$BrowserOnly,
    [switch]$MinerU,
    [switch]$LegacyVenv
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
if ($LegacyVenv -and $PSBoundParameters.ContainsKey('EnvironmentName')) {
    throw 'Choose -LegacyVenv or -EnvironmentName, not both.'
}
$parameters = @{ BrowserOnly = $BrowserOnly; MinerU = $MinerU }
if (-not $LegacyVenv) { $parameters.EnvironmentName = $EnvironmentName }
& (Join-Path $PSScriptRoot 'setup.ps1') @parameters
exit $LASTEXITCODE
