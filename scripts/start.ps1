[CmdletBinding()]
param(
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName,
    [switch]$Desktop,
    [switch]$Browser,
    [Alias('no-open')][switch]$NoOpen,
    [ValidateRange(1, 65535)][int]$Port = 8000,
    [string]$DataDir
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$FrontendIndex = Join-Path $ProjectRoot 'frontend\dist\index.html'
$useConda = -not [string]::IsNullOrWhiteSpace($EnvironmentName)
$serverExitCode = 1
$progressActivity = 'Open InkReference'

function Resolve-CondaExecutable {
    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_EXE) -and (Test-Path -LiteralPath $env:CONDA_EXE -PathType Leaf)) { return [string]$env:CONDA_EXE }
    foreach ($name in @('conda.exe', 'conda.bat', 'conda')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return [string]$command.Source }
    }
    foreach ($directory in @($env:USERPROFILE, $env:LOCALAPPDATA, $env:ProgramData)) {
        if ([string]::IsNullOrWhiteSpace($directory)) { continue }
        foreach ($distribution in @('miniconda3', 'anaconda3', 'miniforge3')) {
            $candidate = Join-Path $directory ($distribution + '\Scripts\conda.exe')
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return [string]$candidate }
        }
    }
    throw 'Conda was not found. Use an Anaconda PowerShell Prompt, or install Miniconda/Miniforge first.'
}

Push-Location -LiteralPath $ProjectRoot
try {
    Write-Progress -Activity $progressActivity -Status 'Checking the frontend and Python environment...' -PercentComplete -1
    Write-Host '[open] Checking the frontend and Python environment...'
    if ($Desktop -and $Browser) { throw 'Choose either -Desktop or -Browser.' }
    if (-not (Test-Path -LiteralPath $FrontendIndex -PathType Leaf)) { throw 'The frontend build was not found. Run scripts\install.ps1 or scripts\setup.cmd first.' }
    $pythonPrefix = @()
    if ($useConda) {
        $pythonExecutable = Resolve-CondaExecutable
        $pythonPrefix = @('run', '--no-capture-output', '-n', $EnvironmentName, 'python')
        Write-Host "Conda environment: $EnvironmentName"
    }
    else {
        if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) { throw 'The project .venv was not found. Run setup.cmd, or use open.ps1 for the Conda environment.' }
        $pythonExecutable = $VenvPython
    }
    $portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
    try { $portProbe.Start() }
    catch { throw "Port $Port is in use or unavailable. Choose another -Port; no existing process was stopped." }
    finally { $portProbe.Stop() }

    $useDesktop = $Desktop -or ($useConda -and -not $Browser)
    $serverArguments = @('-m', 'inkreference', '--host', '127.0.0.1', '--port', [string]$Port)
    if ($useDesktop) { $serverArguments += '--desktop' }
    if ($NoOpen) { $serverArguments += '--no-open' }
    if (-not [string]::IsNullOrWhiteSpace($DataDir)) { $serverArguments += @('--data-dir', $DataDir) }
    Write-Host "InkReference: http://127.0.0.1:$Port"
    if ($useDesktop) { Write-Host '[open] Starting the desktop window. Use -Browser if WebView2 is unavailable.' }
    else { Write-Host '[open] Starting the local browser interface.' }
    Write-Host 'Keep this terminal open. Press Ctrl+C to stop the application.'
    # Hand readiness reporting to the CLI; do not leave a startup progress bar
    # visible for the entire lifetime of the desktop/browser server.
    Write-Progress -Activity $progressActivity -Completed
    $launchArguments = @($pythonPrefix) + $serverArguments
    & $pythonExecutable @launchArguments
    $serverExitCode = $LASTEXITCODE
    if ($serverExitCode -ne 0) { Write-Host "InkReference stopped with exit code $serverExitCode." -ForegroundColor Red }
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    $serverExitCode = 1
}
finally {
    Write-Progress -Activity $progressActivity -Completed
    Pop-Location
}
exit $serverExitCode
