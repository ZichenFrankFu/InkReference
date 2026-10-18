[CmdletBinding()]
param(
    [Alias('CondaEnvironment')]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')][string]$EnvironmentName = 'InkReference',
    [string]$OutputDirectory,
    [switch]$Installer
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$script:FailureExitCode = 1
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$progressActivity = 'Package InkReference'

function Show-Phase {
    param([string]$Status)
    Write-Progress -Activity $progressActivity -Status $Status -PercentComplete -1
    Write-Host ('[package] ' + $Status)
}

function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments, [string]$Description)
    Show-Phase $Description
    & $Executable @Arguments
    $code = $LASTEXITCODE
    if ($code -ne 0) {
        $script:FailureExitCode = $code
        throw "$Description failed (exit code $code)."
    }
}

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
    throw 'Conda was not found. Use an Anaconda PowerShell Prompt or install Miniforge/Miniconda.'
}

function Resolve-InnoCompiler {
    $command = Get-Command 'ISCC.exe' -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($command) { return [string]$command.Source }
    foreach ($directory in @(${env:ProgramFiles(x86)}, $env:ProgramFiles)) {
        if ([string]::IsNullOrWhiteSpace($directory)) { continue }
        foreach ($version in @('7', '6')) {
            $candidate = Join-Path $directory ('Inno Setup ' + $version + '\ISCC.exe')
            if (Test-Path -LiteralPath $candidate -PathType Leaf) { return [string]$candidate }
        }
    }
    throw 'Inno Setup 6.3+ was not found. Install it to build Setup, or omit -Installer to create the portable ZIP only.'
}

Push-Location -LiteralPath $ProjectRoot
try {
    if ($env:OS -ne 'Windows_NT') { throw 'Build Windows packages on Windows.' }
    if ([string]::IsNullOrWhiteSpace($OutputDirectory)) { $OutputDirectory = Join-Path $ProjectRoot 'release' }
    $OutputDirectory = [IO.Path]::GetFullPath($OutputDirectory)
    $conda = Resolve-CondaExecutable
    $inno = if ($Installer) { Resolve-InnoCompiler } else { $null }
    Show-Phase 'Installing the application and building its frontend...'
    & (Join-Path $PSScriptRoot 'install.ps1') -EnvironmentName $EnvironmentName
    $installCode = $LASTEXITCODE
    if ($installCode -ne 0) { $script:FailureExitCode = $installCode; throw 'Application installation failed.' }
    $runPython = @('run', '--no-capture-output', '-n', $EnvironmentName, 'python')
    $pip = @('-m', 'pip', 'install')
    if (Test-Path -LiteralPath (Join-Path $ProjectRoot 'requirements-lock.txt') -PathType Leaf) { $pip += @('-c', 'requirements-lock.txt') }
    $pip += @('-e', '.[desktop,build]')
    Invoke-Checked -Executable $conda -Arguments ($runPython + $pip) -Description 'Installing PyInstaller build dependencies...'

    $applicationDist = Join-Path $OutputDirectory 'app'
    $workDirectory = Join-Path $ProjectRoot 'build\pyinstaller'
    $specDirectory = Join-Path $ProjectRoot 'build\pyinstaller-spec'
    $frontendDirectory = Join-Path $ProjectRoot 'frontend\dist'
    $entry = Join-Path $ProjectRoot 'packaging\desktop_entry.py'
    $buildArguments = @(
        '-m', 'PyInstaller', '--noconfirm', '--clean', '--onedir', '--windowed', '--noupx',
        '--name', 'InkReference', '--distpath', $applicationDist,
        '--workpath', $workDirectory, '--specpath', $specDirectory,
        '--paths', (Join-Path $ProjectRoot 'backend'),
        '--add-data', ($frontendDirectory + ':frontend/dist'),
        '--collect-all', 'inkreference', '--collect-all', 'webview',
        '--collect-all', 'pythonnet', '--collect-all', 'clr_loader',
        '--collect-all', 'langgraph', '--collect-all', 'langgraph.checkpoint.sqlite',
        '--hidden-import', 'inkreference.api', '--hidden-import', 'uvicorn.logging',
        '--hidden-import', 'uvicorn.loops.asyncio', '--hidden-import', 'uvicorn.lifespan.on',
        '--hidden-import', 'uvicorn.protocols.http.h11_impl',
        '--recursive-copy-metadata', 'inkreference', '--copy-metadata', 'pywebview',
        '--recursive-copy-metadata', 'langgraph-checkpoint-sqlite',
        '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6',
        '--exclude-module', 'PySide2', '--exclude-module', 'PySide6',
        '--exclude-module', 'cefpython3', '--exclude-module', 'mineru',
        $entry
    )
    Invoke-Checked -Executable $conda -Arguments ($runPython + $buildArguments) -Description 'Collecting the application, frontend, LangGraph SQLite and desktop runtime...'
    $appDirectory = Join-Path $applicationDist 'InkReference'
    if (-not (Test-Path -LiteralPath (Join-Path $appDirectory 'InkReference.exe') -PathType Leaf)) { throw 'PyInstaller did not produce InkReference.exe.' }
    if (-not (Test-Path -LiteralPath (Join-Path $appDirectory '_internal\frontend\dist\index.html') -PathType Leaf)) { throw 'The bundled frontend is missing.' }
    foreach ($name in @('README.md', 'THIRD_PARTY_NOTICES.md', 'LICENSE')) {
        $source = Join-Path $ProjectRoot $name
        if (Test-Path -LiteralPath $source -PathType Leaf) { Copy-Item -LiteralPath $source -Destination $appDirectory -Force }
    }
    Show-Phase 'Creating the portable ZIP...'
    $zip = Join-Path $OutputDirectory 'InkReference-windows-x64.zip'
    Compress-Archive -LiteralPath $appDirectory -DestinationPath $zip -CompressionLevel Optimal -Force
    Write-Host "Portable application: $zip"

    if ($Installer) {
        Show-Phase 'Reading the application version...'
        $versionOutput = & $conda @runPython -c 'from inkreference import __version__; print(__version__)'
        $versionCode = $LASTEXITCODE
        if ($versionCode -ne 0) { $script:FailureExitCode = $versionCode; throw 'Cannot determine the application version.' }
        $version = $versionOutput | Where-Object { $_ -match '^\d+\.\d+\.\d+(?:[a-zA-Z0-9.+-]*)$' } | Select-Object -Last 1
        if (-not $version) { throw 'The application version is invalid.' }
        $installerDirectory = Join-Path $OutputDirectory 'installer'
        New-Item -ItemType Directory -Path $installerDirectory -Force | Out-Null
        Invoke-Checked -Executable $inno -Arguments @('/Qp', ('/DAppSource=' + $appDirectory), ('/DAppVersion=' + $version), ('/O' + $installerDirectory), (Join-Path $ProjectRoot 'packaging\InkReference.iss')) -Description 'Compiling the Windows Setup installer...'
        if (-not (Test-Path -LiteralPath (Join-Path $installerDirectory ('InkReference-Setup-' + $version + '.exe')) -PathType Leaf)) { throw 'Inno Setup did not produce the expected installer.' }
        Write-Host "Setup installer: $installerDirectory"
    }
    Write-Host 'Packaging completed. No release was published.' -ForegroundColor Green
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
