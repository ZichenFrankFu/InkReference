[CmdletBinding()]
param(
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName,
    [string]$Python,
    [switch]$CurrentEnvironment,
    [string]$OutputDirectory,
    [switch]$Installer,
    [switch]$SkipInstall
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
. (Join-Path $PSScriptRoot 'runtime.ps1')

Push-Location -LiteralPath $ProjectRoot
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Build Windows packages on Windows. On macOS use ./scripts/package.sh.' }
    # Validate all selection flags before changing any environment.
    $selection = @(Get-InkEnvironmentArguments -Python $Python -CurrentEnvironment:$CurrentEnvironment -EnvironmentName $EnvironmentName)
    if (-not $SkipInstall) {
        $installParameters = @{}
        if ($CurrentEnvironment) { $installParameters.CurrentEnvironment = $true }
        if (-not [string]::IsNullOrWhiteSpace($EnvironmentName)) { $installParameters.EnvironmentName = $EnvironmentName }
        if (-not [string]::IsNullOrWhiteSpace($Python)) { $installParameters.Python = $Python }
        & (Join-Path $PSScriptRoot 'install.ps1') @installParameters
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    }
    $bootstrap = Resolve-InkPython -ProjectRoot $ProjectRoot -Python $Python -CurrentEnvironment:$CurrentEnvironment -EnvironmentName $EnvironmentName
    # -Python selects the interpreter that creates .venv during install. Build
    # inside that .venv, never in the base/system interpreter that created it.
    if (-not [string]::IsNullOrWhiteSpace($Python)) { $selection = @() }
    $arguments = @($bootstrap.Prefix) + @((Join-Path $PSScriptRoot 'manage.py'), 'exec') + $selection + @('--', (Join-Path $PSScriptRoot 'package.py'))
    $arguments += if ($SkipInstall) { @('--skip-install') } else { @('--skip-prepare') }
    if ($Installer) { $arguments += '--installer' }
    if (-not [string]::IsNullOrWhiteSpace($OutputDirectory)) { $arguments += @('--output-directory', $OutputDirectory) }
    Write-Progress -Activity 'Package InkReference' -Status 'Building the Windows desktop application...' -PercentComplete -1
    & $bootstrap.Executable @arguments
    $result = $LASTEXITCODE
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    $result = 1
}
finally {
    Write-Progress -Activity 'Package InkReference' -Completed
    Pop-Location
}
exit $result
