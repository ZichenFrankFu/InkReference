[CmdletBinding()]
param(
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName,
    [switch]$BrowserOnly,
    [switch]$MinerU
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$script:FailureExitCode = 1
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDirectory = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvDirectory 'Scripts\python.exe'
$FrontendDirectory = Join-Path $ProjectRoot 'frontend'
$useConda = -not [string]::IsNullOrWhiteSpace($EnvironmentName)
$progressActivity = 'Install InkReference'

function Show-Phase {
    param([string]$Status)
    Write-Progress -Activity $progressActivity -Status $Status -PercentComplete -1
    Write-Host ('[install] ' + $Status)
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$Description
    )
    Show-Phase $Description
    & $Executable @Arguments
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        $script:FailureExitCode = $commandExitCode
        throw "$Description failed (exit code $commandExitCode)."
    }
}

function Resolve-CondaExecutable {
    if (-not [string]::IsNullOrWhiteSpace($env:CONDA_EXE) -and (Test-Path -LiteralPath $env:CONDA_EXE -PathType Leaf)) {
        return [string]$env:CONDA_EXE
    }
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
    throw 'Conda was not found. Install Miniconda/Miniforge, or run this command from an Anaconda PowerShell Prompt. No conda init is required.'
}

function Test-SupportedPython {
    param([string]$Executable, [string[]]$Prefix = @())
    $checkArguments = @($Prefix) + @('-c', 'import sys; raise SystemExit(0 if sys.version_info.major == 3 and 11 <= sys.version_info.minor <= 13 else 1)')
    try {
        & $Executable @checkArguments 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Test-CondaTool {
    param([string]$Executable, [string]$Name, [string]$Tool)
    try {
        $toolOutput = & $Executable run --no-capture-output -n $Name $Tool --version 2>$null
        if ($LASTEXITCODE -ne 0) { return $false }
        if ($Tool -eq 'node') {
            $versionText = ($toolOutput -join "`n").Trim()
            return ($versionText -match '^v(\d+)\.' -and [int]$Matches[1] -ge 22)
        }
        return $true
    }
    catch { return $false }
}

Push-Location -LiteralPath $ProjectRoot
try {
    Show-Phase 'Checking installation inputs...'
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory 'package.json') -PathType Leaf)) {
        throw 'frontend\package.json was not found. Use a complete checkout of InkReference.'
    }
    $pythonPrefix = @()
    $npmPrefix = @()
    if ($useConda) {
        $condaExecutable = Resolve-CondaExecutable
        Write-Host ("Conda: $condaExecutable; environment: $EnvironmentName")
        Show-Phase 'Inspecting Conda environments...'
        $environmentOutput = & $condaExecutable env list --json
        $environmentExitCode = $LASTEXITCODE
        if ($environmentExitCode -ne 0) {
            $script:FailureExitCode = $environmentExitCode
            throw 'Conda could not list environments. Review its output above.'
        }
        $environmentList = ($environmentOutput -join "`n") | ConvertFrom-Json
        $existing = $EnvironmentName -eq 'base'
        foreach ($environmentPath in $environmentList.envs) {
            if ((Split-Path -Leaf $environmentPath) -ieq $EnvironmentName) { $existing = $true; break }
        }
        if (-not $existing) {
            Invoke-Checked -Executable $condaExecutable -Arguments @('create', '--name', $EnvironmentName, '--yes', '--override-channels', '--channel', 'conda-forge', 'python=3.12', 'pip', 'nodejs=22') -Description 'Creating the Conda environment with Python 3.12 and Node.js 22...'
        }
        else { Write-Host "Reusing Conda environment: $EnvironmentName" }

        Show-Phase 'Checking Python in the selected Conda environment...'
        $versionOutput = & $condaExecutable run --no-capture-output -n $EnvironmentName python -c 'import sys; print(sys.version_info.major, sys.version_info.minor, sep=chr(46)); raise SystemExit(0 if sys.version_info.major == 3 and 11 <= sys.version_info.minor <= 13 else 2)'
        $versionExitCode = $LASTEXITCODE
        if ($versionExitCode -ne 0) {
            $script:FailureExitCode = $versionExitCode
            throw "Environment $EnvironmentName needs Python 3.11, 3.12, or 3.13. Its Python was not changed."
        }
        $pythonMinor = $versionOutput | Where-Object { $_ -match '^3\.(11|12|13)$' } | Select-Object -Last 1
        if (-not $pythonMinor) { throw 'Could not determine the selected Conda Python version.' }
        Show-Phase 'Checking pip, Node.js and npm in the Conda environment...'
        $missingDependencies = @()
        if (-not (Test-CondaTool -Executable $condaExecutable -Name $EnvironmentName -Tool 'node') -or -not (Test-CondaTool -Executable $condaExecutable -Name $EnvironmentName -Tool 'npm.cmd')) {
            $missingDependencies += 'nodejs=22'
        }
        $hasPip = $false
        try {
            & $condaExecutable run --no-capture-output -n $EnvironmentName python -m pip --version 2>$null | Out-Null
            $hasPip = $LASTEXITCODE -eq 0
        }
        catch { $hasPip = $false }
        if (-not $hasPip) { $missingDependencies += 'pip' }
        if ($missingDependencies.Count -gt 0) {
            Invoke-Checked -Executable $condaExecutable -Arguments (@('install', '--name', $EnvironmentName, '--yes', '--override-channels', '--channel', 'conda-forge', ('python=' + $pythonMinor)) + $missingDependencies) -Description 'Installing missing tools while preserving the Python minor version...'
        }
        $pythonExecutable = $condaExecutable
        $pythonPrefix = @('run', '--no-capture-output', '-n', $EnvironmentName, 'python')
        $npmExecutable = $condaExecutable
        $npmPrefix = @('run', '--no-capture-output', '-n', $EnvironmentName, 'npm.cmd')
    }
    else {
        # Select one ApplicationInfo even when multiple PATH entries match.
        $nodeCommand = Get-Command 'node.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        $npmCommand = Get-Command 'npm.cmd' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $nodeCommand -or -not $npmCommand) {
            throw 'Legacy .venv mode needs Node.js 22 or 24 on PATH. Use install.ps1 for a Conda environment with Node.js included.'
        }
        if (Test-Path -LiteralPath $VenvDirectory) {
            if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
                throw 'The project .venv is incomplete. Rename it, then run setup.cmd again.'
            }
            if (-not (Test-SupportedPython -Executable $VenvPython)) {
                throw 'The existing .venv needs Python 3.11, 3.12, or 3.13. Rename it before recreating this environment.'
            }
            Write-Host 'Using the existing project .venv.'
        }
        else {
            $pythonCandidate = $null
            $pyCommand = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($pyCommand) {
                foreach ($version in @('-3.12', '-3.11', '-3.13')) {
                    if (Test-SupportedPython -Executable $pyCommand.Source -Prefix @($version)) {
                        $pythonCandidate = @{ Executable = $pyCommand.Source; Prefix = @($version) }
                        break
                    }
                }
            }
            if (-not $pythonCandidate) {
                foreach ($name in @('python.exe', 'python3.exe')) {
                    $candidateCommand = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
                    if ($candidateCommand -and (Test-SupportedPython -Executable $candidateCommand.Source)) {
                        $pythonCandidate = @{ Executable = $candidateCommand.Source; Prefix = @() }
                        break
                    }
                }
            }
            if (-not $pythonCandidate) { throw 'Install Python 3.11-3.13, or use install.ps1 with Conda.' }
            Invoke-Checked -Executable $pythonCandidate.Executable -Arguments (@($pythonCandidate.Prefix) + @('-m', 'venv', $VenvDirectory)) -Description 'Creating the project .venv...'
        }
        $pythonExecutable = $VenvPython
        $npmExecutable = [string]$npmCommand.Source
    }

    $pipArguments = @('-m', 'pip', 'install')
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'requirements-lock.txt') -PathType Leaf) { $pipArguments += @('-c', 'requirements-lock.txt') }
    $extras = if ($BrowserOnly) { '.[dev]' } else { '.[dev,desktop]' }
    $pipArguments += @('-e', $extras)
    Invoke-Checked -Executable $pythonExecutable -Arguments (@($pythonPrefix) + $pipArguments) -Description 'Installing InkReference Python dependencies (download logs follow)...'
    if ($MinerU) {
        $mineruRequirements = Join-Path $ProjectRoot 'requirements-mineru.txt'
        if (-not (Test-Path -LiteralPath $mineruRequirements -PathType Leaf)) { throw 'requirements-mineru.txt was not found in this checkout.' }
        # MinerU is optional and has a separate requirements set. Do not apply
        # the application's base constraints to its dependency resolution.
        Invoke-Checked -Executable $pythonExecutable -Arguments (@($pythonPrefix) + @('-m', 'pip', 'install', '-r', $mineruRequirements)) -Description 'Installing optional MinerU dependencies (downloads may take several minutes)...'
    }
    Push-Location -LiteralPath $FrontendDirectory
    try {
        $installCommand = if (Test-Path -LiteralPath (Join-Path $FrontendDirectory 'package-lock.json') -PathType Leaf) { 'ci' } else { 'install' }
        Invoke-Checked -Executable $npmExecutable -Arguments (@($npmPrefix) + @($installCommand)) -Description 'Installing frontend dependencies (download logs follow)...'
        Invoke-Checked -Executable $npmExecutable -Arguments (@($npmPrefix) + @('run', 'build')) -Description 'Building the frontend...'
    }
    finally { Pop-Location }
    if ($useConda) { Write-Host "Setup completed. Run scripts\open.ps1 -EnvironmentName $EnvironmentName" -ForegroundColor Green }
    else { Write-Host 'Setup completed. Run scripts\start.cmd to use the existing .venv launcher.' -ForegroundColor Green }
    if ($BrowserOnly) { Write-Host 'Desktop dependencies were skipped. Open with -Browser.' }
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit $script:FailureExitCode
}
finally {
    Write-Progress -Activity $progressActivity -Completed
    Pop-Location
}
exit 0
