#Requires -Version 7.4
# Shared Windows paths for setup.ps1 and run.ps1; no robot connection is made.

function Initialize-Ur5eEnvironment {
    if (-not $IsWindows) {
        throw 'These PowerShell scripts target native Windows. Use scripts/run.sh on macOS or Linux.'
    }
    if (-not (Get-Command uv -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'uv was not found. Install uv, reopen PowerShell, and follow docs/WINDOWS.md.'
    }

    $repositoryRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
    $identity = $repositoryRoot.TrimEnd([char[]]'\/').ToLowerInvariant()
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $hash = $sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($identity))
        $projectId = ([BitConverter]::ToString($hash)).Replace('-', '').Substring(0, 12).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }

    $localRoot = [Environment]::GetFolderPath([Environment+SpecialFolder]::LocalApplicationData)
    if ([string]::IsNullOrWhiteSpace($localRoot)) {
        throw 'Windows LocalApplicationData is unavailable. A persistent local data directory is required.'
    }
    $projectRoot = Join-Path $localRoot "UR5eConnector\$projectId"
    $runtimeRoot = if ($env:UR5E_RUNTIME_DIR) { $env:UR5E_RUNTIME_DIR } else { Join-Path $projectRoot 'runtime' }
    $dataRoot = if ($env:UR5E_DATA_DIR) { $env:UR5E_DATA_DIR } else { Join-Path $projectRoot 'data' }
    foreach ($path in @($runtimeRoot, $dataRoot)) {
        if (-not [IO.Path]::IsPathFullyQualified($path)) {
            throw 'UR5E_RUNTIME_DIR and UR5E_DATA_DIR overrides must be absolute Windows paths.'
        }
    }
    $runtimeRoot = [IO.Path]::GetFullPath($runtimeRoot)
    $dataRoot = [IO.Path]::GetFullPath($dataRoot)
    $null = New-Item -ItemType Directory -Force -Path $runtimeRoot, $dataRoot
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $runtimeRoot 'venv'
    $env:PYTHONPYCACHEPREFIX = Join-Path $runtimeRoot 'pycache'
    $env:UR5E_DATA_DIR = $dataRoot
    $env:PYTHONUTF8 = '1'

    [PSCustomObject]@{
        RepositoryRoot = $repositoryRoot
        Python = Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe'
    }
}
