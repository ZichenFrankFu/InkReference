[CmdletBinding()]
param()

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$script:FailureExitCode = 1
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$VenvDirectory = Join-Path $ProjectRoot '.venv'
$VenvPython = Join-Path $VenvDirectory 'Scripts\python.exe'
$FrontendDirectory = Join-Path $ProjectRoot 'frontend'

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$Description
    )
    Write-Host $Description
    & $Executable @Arguments
    $commandExitCode = $LASTEXITCODE
    if ($commandExitCode -ne 0) {
        $script:FailureExitCode = $commandExitCode
        throw "$Description failed (exit code $commandExitCode)."
    }
}

function Test-SupportedPython {
    param([string]$Executable, [string[]]$Prefix = @())
    # Keep candidate detection quiet: an unavailable py version is expected.
    $checkArguments = @($Prefix) + @('-c', 'import sys; raise SystemExit(0 if sys.version_info.major == 3 and 11 <= sys.version_info.minor <= 13 else 1)')
    try {
        & $Executable @checkArguments 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch {
        return $false
    }
}

Push-Location -LiteralPath $ProjectRoot
try {
    $nodeCommand = Get-Command 'node.exe' -CommandType Application -ErrorAction SilentlyContinue
    $npmCommand = Get-Command 'npm.cmd' -CommandType Application -ErrorAction SilentlyContinue
    if (-not $nodeCommand -or -not $npmCommand) {
        throw 'Node.js and npm are required. Install Node.js 22 or 24, reopen the terminal, and run setup.cmd again.'
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory 'package.json') -PathType Leaf)) {
        throw 'frontend\package.json was not found. Run setup from a complete checkout of InkReference.'
    }

    if (Test-Path -LiteralPath $VenvDirectory) {
        if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) {
            throw 'The project .venv is incomplete. Rename or remove this project environment, then run setup.cmd again.'
        }
        if (-not (Test-SupportedPython -Executable $VenvPython)) {
            throw 'The existing .venv needs Python 3.11, 3.12, or 3.13. Rename it and rerun setup.cmd with a supported Python installed.'
        }
        Write-Host 'Using the existing project .venv.'
    }
    else {
        $pythonCandidate = $null
        $pyCommand = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue
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
                $candidateCommand = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
                if ($candidateCommand -and (Test-SupportedPython -Executable $candidateCommand.Source)) {
                    $pythonCandidate = @{ Executable = $candidateCommand.Source; Prefix = @() }
                    break
                }
            }
        }
        if (-not $pythonCandidate) {
            throw 'Python 3.11, 3.12, or 3.13 was not found. Install Python with the Windows py launcher, reopen the terminal, and retry.'
        }
        $venvArguments = @($pythonCandidate.Prefix) + @('-m', 'venv', $VenvDirectory)
        Invoke-Checked -Executable $pythonCandidate.Executable -Arguments $venvArguments -Description 'Creating the project .venv...'
    }

    $pipArguments = @('-m', 'pip', 'install')
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'requirements-lock.txt') -PathType Leaf) {
        $pipArguments += @('-c', 'requirements-lock.txt')
    }
    $pipArguments += @('-e', '.[dev]')
    Invoke-Checked -Executable $VenvPython -Arguments $pipArguments -Description 'Installing InkReference and development dependencies...'
    Push-Location -LiteralPath $FrontendDirectory
    try {
        if (Test-Path -LiteralPath (Join-Path $FrontendDirectory 'package-lock.json') -PathType Leaf) {
            Invoke-Checked -Executable $npmCommand.Source -Arguments @('ci') -Description 'Installing frontend dependencies from package-lock.json...'
        }
        else {
            Invoke-Checked -Executable $npmCommand.Source -Arguments @('install') -Description 'Installing frontend dependencies...'
        }
        Invoke-Checked -Executable $npmCommand.Source -Arguments @('run', 'build') -Description 'Building the frontend...'
    }
    finally {
        Pop-Location
    }
    Write-Host 'Setup completed. Run scripts\start.cmd to open InkReference.' -ForegroundColor Green
}
catch {
    Write-Host ('ERROR: ' + $_.Exception.Message) -ForegroundColor Red
    exit $script:FailureExitCode
}
finally {
    Pop-Location
}
exit 0
