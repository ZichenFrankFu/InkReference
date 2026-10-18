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

# Compatibility entry: installation is implemented once in manage.py.
& (Join-Path $PSScriptRoot 'install.ps1') @PSBoundParameters
exit $LASTEXITCODE
