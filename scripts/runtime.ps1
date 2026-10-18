# Shared source-launcher bootstrap. Never activates or creates Conda environments.
Set-StrictMode -Version 2.0

function Test-InkPython {
    param([string]$Executable, [string[]]$Prefix = @())
    try {
        $arguments = @($Prefix) + @('-c', 'import sys; raise SystemExit(0 if sys.version_info.major == 3 and 11 <= sys.version_info.minor <= 13 else 1)')
        & $Executable @arguments 2>$null | Out-Null
        return ($LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Resolve-InkPython {
    param(
        [Parameter(Mandatory = $true)][string]$ProjectRoot,
        [string]$Python,
        [switch]$CurrentEnvironment,
        [string]$EnvironmentName
    )
    if (-not [string]::IsNullOrWhiteSpace($Python)) {
        $candidate = $Python
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $command = Get-Command $Python -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $candidate = [string]$command.Source }
        }
        if (-not (Test-InkPython -Executable $candidate)) { throw 'The selected executable needs Python 3.11, 3.12 or 3.13.' }
        return [PSCustomObject]@{ Executable = $candidate; Prefix = @() }
    }
    if (-not [string]::IsNullOrWhiteSpace($EnvironmentName)) {
        $conda = $null
        if (-not [string]::IsNullOrWhiteSpace($env:CONDA_EXE) -and (Test-Path -LiteralPath $env:CONDA_EXE -PathType Leaf)) { $conda = $env:CONDA_EXE }
        if (-not $conda) {
            $command = Get-Command 'conda' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command) { $conda = [string]$command.Source }
        }
        if (-not $conda) { throw 'Conda was explicitly selected but was not found. Activate your existing environment and use -CurrentEnvironment, or use the default project .venv.' }
        return [PSCustomObject]@{ Executable = [string]$conda; Prefix = @('run', '--no-capture-output', '-n', $EnvironmentName, 'python') }
    }
    $venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    if (-not $CurrentEnvironment -and (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        if (-not (Test-InkPython -Executable $venvPython)) { throw 'The project .venv needs Python 3.11-3.13. Rename the old .venv before recreating it.' }
        return [PSCustomObject]@{ Executable = $venvPython; Prefix = @() }
    }
    # PATH is authoritative for an explicitly selected active environment.
    if ($CurrentEnvironment) {
        foreach ($name in @('python.exe', 'python3.exe', 'python', 'python3')) {
            $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($command -and (Test-InkPython -Executable $command.Source)) {
                return [PSCustomObject]@{ Executable = [string]$command.Source; Prefix = @() }
            }
        }
        throw 'The current environment needs Python 3.11-3.13. Activate the environment you want to use, then retry.'
    }
    $launcher = Get-Command 'py.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($launcher) {
        foreach ($version in @('-3.12', '-3.11', '-3.13')) {
            if (Test-InkPython -Executable $launcher.Source -Prefix @($version)) {
                return [PSCustomObject]@{ Executable = [string]$launcher.Source; Prefix = @($version) }
            }
        }
    }
    foreach ($name in @('python.exe', 'python3.exe', 'python', 'python3')) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command -and (Test-InkPython -Executable $command.Source)) {
            return [PSCustomObject]@{ Executable = [string]$command.Source; Prefix = @() }
        }
    }
    throw 'Install Python 3.11-3.13 or select its executable with -Python. Packaged apps do not need Python or Conda.'
}

function Get-InkEnvironmentArguments {
    param([string]$Python, [switch]$CurrentEnvironment, [string]$EnvironmentName)
    $selected = 0
    if ($CurrentEnvironment) { $selected++ }
    if (-not [string]::IsNullOrWhiteSpace($Python)) { $selected++ }
    if (-not [string]::IsNullOrWhiteSpace($EnvironmentName)) { $selected++ }
    if ($selected -gt 1) { throw 'Choose only one of -CurrentEnvironment, -Python or -EnvironmentName.' }
    if ($CurrentEnvironment) { return @('--current-environment') }
    if (-not [string]::IsNullOrWhiteSpace($Python)) { return @('--python', $Python) }
    if (-not [string]::IsNullOrWhiteSpace($EnvironmentName)) { return @('--conda-environment', $EnvironmentName) }
    return @()
}
