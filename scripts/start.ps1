[CmdletBinding()]
param(
    [Alias('no-open')][switch]$NoOpen,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$DataDir
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$FrontendIndex = Join-Path $ProjectRoot 'frontend\dist\index.html'
$serverExitCode = 1

Push-Location -LiteralPath $ProjectRoot
try {
    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
        throw 'The project Python environment was not found. Run scripts\setup.cmd first.'
    }
    if (-not (Test-Path -LiteralPath $FrontendIndex -PathType Leaf)) {
        throw 'The frontend build was not found. Run scripts\setup.cmd first.'
    }

    # Probe loopback only. Do not stop or alter the process owning a busy port.
    $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try {
        $portProbe.Start()
    }
    catch {
        throw "Port $Port is already in use or unavailable. Close the other application yourself or run start.cmd -Port 8001."
    }
    finally {
        $portProbe.Stop()
    }

    $serverArguments = @('-m', 'inkreference', '--host', '127.0.0.1', '--port', [string]$Port)
    if ($NoOpen) {
        $serverArguments += '--no-open'
    }
    if (-not [string]::IsNullOrWhiteSpace($DataDir)) {
        $serverArguments += @('--data-dir', $DataDir)
    }
    Write-Host "InkReference: http://127.0.0.1:$Port"
    Write-Host 'Keep this terminal open. Press Ctrl+C to stop the application.'
    & $VenvPython @serverArguments
    $serverExitCode = $LASTEXITCODE
    if ($serverExitCode -ne 0) {
        Write-Host "InkReference stopped with exit code $serverExitCode." -ForegroundColor Red
    }
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    $serverExitCode = 1
}
finally {
    Pop-Location
}
exit $serverExitCode
