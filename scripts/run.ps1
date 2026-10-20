#Requires -Version 7.4
# Forward arguments to uv in the same local environment as setup.ps1.
$ErrorActionPreference = 'Stop'
$PSNativeCommandArgumentPassing = 'Standard'
$PSNativeCommandUseErrorActionPreference = $false
$commandArguments = $args
. (Join-Path $PSScriptRoot 'environment.ps1')
$environment = Initialize-Ur5eEnvironment
if (-not (Test-Path -LiteralPath $environment.Python -PathType Leaf)) {
    throw 'Run .\scripts\setup.ps1 first; the Windows Python environment is missing.'
}
if ($commandArguments.Count -eq 0) {
    throw 'Specify a command, for example: .\scripts\run.ps1 python examples/demo_client.py'
}
Push-Location -LiteralPath $environment.RepositoryRoot
try {
    & uv run --locked --no-sync @commandArguments
    $commandExitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}
exit $commandExitCode
