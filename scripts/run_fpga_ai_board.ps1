[CmdletBinding()]
param(
    [string]$QuartusRoot = "C:\altera_pro\26.1\quartus",
    [string]$NiosBin     = "C:\altera_pro\26.1\niosv\bin",
    [string]$Cable       = "USB Blaster III",
    [int]$CaptureSeconds = 150,
    [Parameter(Mandatory = $true)][string]$OutFile
)

# Program the board, download the Nios V ELF and capture the JTAG UART for a
# bounded time.  Every step is bounded so a dead cable cannot hang the run.

$ErrorActionPreference = "Stop"
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$sof  = Join-Path $repo "fpga\axc3000_mlperf\output_files\axc3000_top.sof"
$elf  = Join-Path $repo "fpga\axc3000_mlperf\software\fpga_ai_resnet8\generated\app\build\release\fpga_ai_resnet8.elf"

foreach ($f in @($sof, $elf)) {
    if (-not (Test-Path -LiteralPath $f)) { throw "missing $f" }
}

Write-Host "sof=$sof"
Write-Host ("sof_mtime=" + (Get-Item $sof).LastWriteTime.ToString("s"))
Write-Host ("elf_mtime=" + (Get-Item $elf).LastWriteTime.ToString("s"))

& "$QuartusRoot\bin64\quartus_pgm.exe" -c $Cable -m jtag -o "p;$sof"
if ($LASTEXITCODE -ne 0) { throw "quartus_pgm failed ($LASTEXITCODE)" }

# niosv-download insists on forward slashes.
$elfFwd = $elf -replace '\\', '/'
& "$NiosBin\niosv-download.exe" -g $elfFwd
if ($LASTEXITCODE -ne 0) { throw "niosv-download failed ($LASTEXITCODE)" }

& "$QuartusRoot\bin64\juart-terminal.exe" -c $Cable -o $CaptureSeconds 2>&1 |
    Tee-Object -FilePath $OutFile
Write-Host "capture=$OutFile"
