[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [switch]$IncludeLocalEnv
)

$cleanupScript = Get-Content -LiteralPath (Join-Path $PSScriptRoot "setup.ps2") -Raw
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
& ([scriptblock]::Create($cleanupScript)) -IncludeLocalEnv:$IncludeLocalEnv -ProjectRoot $projectRoot
