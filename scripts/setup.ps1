#Requires -Version 7.4
# Install the locked development environment without starting a connector.
$ErrorActionPreference = 'Stop'
$PSNativeCommandArgumentPassing = 'Standard'
$PSNativeCommandUseErrorActionPreference = $false
$setupArguments = $args
. (Join-Path $PSScriptRoot 'environment.ps1')
$environment = Initialize-Ur5eEnvironment
Push-Location -LiteralPath $environment.RepositoryRoot
try {
    & uv sync --locked --all-extras --link-mode copy @setupArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Dependency installation failed with exit code $LASTEXITCODE. No connector was started."
    }
    & uv run --locked --no-sync python -c 'import unitelabs.ur5e_connector; print("UR5e connector environment ready")'
    if ($LASTEXITCODE -ne 0) {
        throw "Connector import failed with exit code $LASTEXITCODE. No connector was started."
    }
    Write-Host "Runtime data: $env:UR5E_DATA_DIR"
}
finally {
    Pop-Location
}
