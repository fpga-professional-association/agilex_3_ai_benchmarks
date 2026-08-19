[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$target = Join-Path $repoRoot 'build\fpga_ai\python_deps'
$requirements = Join-Path $repoRoot 'requirements-fpga-ai.txt'

New-Item -ItemType Directory -Force -Path $target | Out-Null
$command = (
    'call "{0}" && python -m pip install --disable-pip-version-check ' +
    '--no-deps --target "{1}" -r "{2}"'
) -f (Join-Path $PSScriptRoot 'ai_suite_env.cmd'), $target, $requirements
& cmd.exe /d /s /c $command
if ($LASTEXITCODE -ne 0) {
    throw "FPGA AI Suite Python dependency install failed: $LASTEXITCODE"
}
