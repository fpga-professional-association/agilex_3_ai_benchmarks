param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..\..")),
    [switch]$KeepGenerated
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path $RepoRoot).Path
$niosBin = "C:\altera_pro\26.1\niosv\bin"
$riscvBin = "C:\altera_pro\26.1\riscfree\toolchain\riscv32-unknown-elf\bin"
$cmakeBin = "C:\altera_pro\26.1\riscfree\build_tools\cmake\bin"
$env:Path = "$niosBin;$riscvBin;$cmakeBin;$env:Path"
$sopcinfo = Join-Path $root "fpga\axc3000_mlperf\NIOSV_lab\NIOSV_lab.sopcinfo"
$build = Join-Path $root "software\resnet8\generated\niosv"
$bsp = Join-Path $build "bsp"
$app = Join-Path $build "app"
$appBuild = Join-Path $app "build\release"
$elf = Join-Path $appBuild "resnet8.elf"
$map = Join-Path $appBuild "resnet8.map"
$report = Join-Path $root "reports\resnet8_niosv_memory.txt"

function Find-Tool([string]$name) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($base in @($env:QUARTUS_ROOTDIR, $env:QUARTUS_ROOTDIR_OVERRIDE,
                        "C:\intelFPGA_pro\26.1", "C:\intelFPGA_lite\26.1", "C:\altera_pro\26.1")) {
        if (-not $base) { continue }
        foreach ($p in @((Join-Path $base "niosv\bin\$name.bat"),
                         (Join-Path $base "niosv\bin\$name.exe"),
                         (Join-Path $base "riscfree\toolchain\riscv32-unknown-elf\bin\$name.exe"),
                         (Join-Path $base "riscfree\build_tools\cmake\bin\$name.exe"),
                         (Join-Path $base "bin64\$name.exe"))) {
            if (Test-Path -LiteralPath $p) { return $p }
        }
    }
    throw "Required tool '$name' was not found. Source the Quartus/Nios V 26.1 environment first."
}

if (-not (Test-Path -LiteralPath $sopcinfo)) { throw "Missing SOPCINFO: $sopcinfo" }
python (Join-Path $root "tools\generate_resnet8_model.py")
$xml = [xml](Get-Content -LiteralPath $sopcinfo -Raw)
$memory = $xml.SelectSingleNode("//*[local-name()='memoryBlock']/*[local-name()='moduleName' and text()='onchip_memory']/following-sibling::*[local-name()='baseAddress'][1]")
$span = $xml.SelectSingleNode("//*[local-name()='memoryBlock']/*[local-name()='moduleName' and text()='onchip_memory']/following-sibling::*[local-name()='span'][1]")
if (-not $memory -or [int64]$memory.'#text' -ne 0 -or -not $span -or [int64]$span.'#text' -ne 524288) {
    throw "SOPCINFO on-chip memory is not the corrected 0x0..0x80000 span"
}
$jtag = $xml.SelectNodes("//*[local-name()='baseAddress']") | Where-Object { [int64]$_.InnerText -eq 589896 }
if (-not $jtag) { throw "SOPCINFO JTAG UART base 0x90048 was not found" }

$niosvBsp = Find-Tool "niosv-bsp"
$niosvApp = Find-Tool "niosv-app"
$cmake = Find-Tool "cmake"
$size = Find-Tool "riscv32-unknown-elf-size"
New-Item -ItemType Directory -Force -Path $build | Out-Null
$qsys = Join-Path $root "fpga\axc3000_mlperf\NIOSV_lab.qsys"
$qpf = Join-Path $root "fpga\axc3000_mlperf\axc3000_top.qpf"
$settings = Join-Path $bsp "settings.bsp"
& $niosvBsp $settings --create "--qsys=$qsys" "--quartus-project=$qpf" --type=hal "--bsp-dir=$bsp" `
    '--cmd=add_section_mapping .bss onchip_memory' `
    '--cmd=add_section_mapping .heap onchip_memory' `
    '--cmd=add_section_mapping .rodata onchip_memory' `
    '--cmd=add_section_mapping .rwdata onchip_memory' `
    '--cmd=add_section_mapping .stack onchip_memory' `
    '--cmd=add_section_mapping .text onchip_memory' `
    '--cmd=add_section_mapping .exceptions onchip_memory'
if ($LASTEXITCODE -ne 0) { throw "niosv-bsp failed ($LASTEXITCODE)" }
$sources = @(
    (Join-Path $root "software\resnet8\resnet8.c"),
    (Join-Path $root "software\resnet8\benchmark.c"),
    (Join-Path $root "software\resnet8\niosv_main.c"),
    (Join-Path $root "software\resnet8\niosv_adapter.c")
) -join ","
& $niosvApp "--app-dir=$app" "--bsp-dir=$bsp" "--srcs=$sources" "--incs=$(Join-Path $root 'software\resnet8')" "--elf-name=resnet8.elf"
if ($LASTEXITCODE -ne 0) { throw "niosv-app failed ($LASTEXITCODE)" }
& $cmake -G "Unix Makefiles" -DCMAKE_BUILD_TYPE=Release -B $appBuild -S $app `
    "-DCMAKE_C_FLAGS=-O3 -ffunction-sections -fdata-sections" `
    "-DCMAKE_EXE_LINKER_FLAGS=-Wl,-Map=$map"
if ($LASTEXITCODE -ne 0) { throw "CMake configure failed ($LASTEXITCODE)" }
& $cmake --build $appBuild --parallel
if ($LASTEXITCODE -ne 0) { throw "CMake build failed ($LASTEXITCODE)" }
if (-not (Test-Path -LiteralPath $elf)) { throw "ELF was not produced: $elf" }

$sizeText = & $size -A $elf | Out-String
$sectionSizes = @{}
foreach ($line in ($sizeText -split "`r?`n")) {
    if ($line -match '^\s*(\.[A-Za-z0-9_]+)\s+([0-9]+)\s+') {
        $sectionSizes[$Matches[1]] = [uint64]$Matches[2]
    }
}
function SectionSum([string[]]$names) {
    [uint64]$sum = 0
    foreach ($name in $names) { if ($sectionSizes.ContainsKey($name)) { $sum += $sectionSizes[$name] } }
    return $sum
}
$startupBytes = SectionSum @('.entry','.exceptions')
$codeBytes = SectionSum @('.text','.init','.fini','.vectors')
$rodataBytes = SectionSum @('.rodata','.srodata')
$dataBytes = SectionSum @('.data','.sdata','.rwdata')
$bssBytes = SectionSum @('.bss','.sbss')
$usedBytes = $codeBytes + $rodataBytes + $dataBytes + $bssBytes
$freeBytes = if ($usedBytes -le 524288) { [uint64]524288 - $usedBytes } else { [int64]-( $usedBytes - 524288 ) }
$mapText = Get-Content -LiteralPath $map -Raw
function MapSymbol([string[]]$names) {
    foreach ($name in $names) {
        $m = [regex]::Match($mapText, "(?m)^\s*(0x[0-9a-fA-F]+)\s+.*\b${name}\b.*$")
        if ($m.Success) { return [Convert]::ToUInt64($m.Groups[1].Value.Substring(2), 16) }
    }
    return $null
}
$heapStart = MapSymbol @('_heap_start','__heap_start','__alt_heap_start')
$heapEnd = MapSymbol @('_heap_end','__heap_end','__alt_heap_limit')
$stackBottom = MapSymbol @('_stack_bottom','__stack_bottom','_end','__end','__alt_stack_base')
$stackTop = MapSymbol @('_stack','__stack','_stack_top','__stack_top','__alt_stack_pointer')
$heapBytes = if ($null -ne $heapStart -and $null -ne $heapEnd) { [int64]$heapEnd - [int64]$heapStart } else { $null }
$stackBytes = if ($null -ne $stackBottom -and $null -ne $stackTop) { [int64]$stackTop - [int64]$stackBottom } else { $null }
Set-Content -LiteralPath $report -Value @(
    "ResNet8 Nios V memory report"
    "SOPCINFO: $sopcinfo"
    "on-chip memory: base=0x0 span=0x80000 (524288 bytes)"
    "JTAG UART: base=0x90048"
    "timer/timestamp: Nios V internal timer, expected 100000000 Hz"
    "ELF: $elf"
    "MAP: $map"
    "code_bytes=$codeBytes rodata_bytes=$rodataBytes data_bytes=$dataBytes bss_bytes=$bssBytes"
    "startup_entry_exceptions_bytes=$startupBytes link_end=$heapStart"
    "static_used_bytes=$usedBytes static_free_bytes=$freeBytes capacity_bytes=524288"
    "shared_stack_heap_bytes=$stackBytes stack_bottom=$stackBottom stack_top=$stackTop"
    "shared_heap_window_bytes=$heapBytes heap_start=$heapStart heap_end=$heapEnd"
    ""
    "riscv32-unknown-elf-size -A:"
    $sizeText
    ""
    "Section accounting is taken from the linker map/size output above."
    "stack and heap share the BSP window; the two exact symbol-derived window values above must not be added together."
) -Encoding UTF8
Write-Host "ELF=$elf"
Write-Host "MAP=$map"
Write-Host "REPORT=$report"
if (-not $KeepGenerated) { Write-Host "Generated build retained under ignored software/resnet8/generated/ for inspection." }
