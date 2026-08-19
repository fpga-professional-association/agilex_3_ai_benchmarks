param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..\..\..")),
    [switch]$Clean
)

$ErrorActionPreference = "Stop"
$sw = [Diagnostics.Stopwatch]::StartNew()
$root = (Resolve-Path $RepoRoot).Path
$firmware = Join-Path $root "fpga\axc3000_mlperf\software\fpga_ai_resnet8"
$sopcinfo = Join-Path $root "fpga\axc3000_mlperf\NIOSV_lab\NIOSV_lab.sopcinfo"
$qsys = Join-Path $root "fpga\axc3000_mlperf\NIOSV_lab.qsys"
$qpf = Join-Path $root "fpga\axc3000_mlperf\axc3000_top.qpf"
$generated = Join-Path $firmware "generated"
$bsp = Join-Path $generated "bsp"
$app = Join-Path $generated "app"
$appBuild = Join-Path $app "build\release"
$elf = Join-Path $appBuild "fpga_ai_resnet8.elf"
$map = Join-Path $appBuild "fpga_ai_resnet8.map"

$niosBin = "C:\altera_pro\26.1\niosv\bin"
$riscvBin = "C:\altera_pro\26.1\riscfree\toolchain\riscv32-unknown-elf\bin"
$cmakeBin = "C:\altera_pro\26.1\riscfree\build_tools\cmake\bin"
$env:Path = "$niosBin;$riscvBin;$cmakeBin;$env:Path"

foreach ($required in @($sopcinfo, $qsys, $qpf)) {
    if (-not (Test-Path -LiteralPath $required)) { throw "Missing required design file: $required" }
}
if ($Clean -and (Test-Path -LiteralPath $generated)) {
    Remove-Item -LiteralPath $generated -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $generated | Out-Null

function Run-Tool([string]$exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe failed with exit code $LASTEXITCODE" }
}

$settings = Join-Path $bsp "settings.bsp"
Run-Tool (Join-Path $niosBin "niosv-bsp.exe") @(
    $settings, "--create", "--qsys=$qsys", "--quartus-project=$qpf", "--type=hal", "--bsp-dir=$bsp",
    "--cmd=add_section_mapping .bss onchip_memory",
    "--cmd=add_section_mapping .heap onchip_memory",
    "--cmd=add_section_mapping .rodata onchip_memory",
    "--cmd=add_section_mapping .rwdata onchip_memory",
    "--cmd=add_section_mapping .stack onchip_memory",
    "--cmd=add_section_mapping .text onchip_memory",
    "--cmd=add_section_mapping .exceptions onchip_memory"
)

$source = Join-Path $firmware "main.c"
Run-Tool (Join-Path $niosBin "niosv-app.exe") @(
    "--app-dir=$app", "--bsp-dir=$bsp", "--srcs=$source", "--incs=$firmware", "--elf-name=fpga_ai_resnet8.elf"
)
Run-Tool (Join-Path $cmakeBin "cmake.exe") @(
    "-G", "Unix Makefiles", "-DCMAKE_BUILD_TYPE=Release",
    "-DCMAKE_C_FLAGS=-Os -ffunction-sections -fdata-sections",
    "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-Map=$map",
    "-B", $appBuild, "-S", $app
)
Run-Tool (Join-Path $cmakeBin "cmake.exe") @("--build", $appBuild, "--parallel")

if (-not (Test-Path -LiteralPath $elf)) { throw "ELF was not produced: $elf" }
$sizeExe = Join-Path $riscvBin "riscv32-unknown-elf-size.exe"
$sizeText = (& $sizeExe -A $elf | Out-String)
$sw.Stop()
Write-Host "ELF=$elf"
Write-Host "MAP=$map"
Write-Host $sizeText
Write-Host ("build_wall_seconds={0:R}" -f $sw.Elapsed.TotalSeconds)
Write-Host "target_run=not_performed"
