# Phase 13 compile 1: compile ResNet-8 for the k16c16 on-chip-parameter arch and
# regenerate the CoreDLA IP (including the on-chip parameter ROM MIFs).
# Does NOT touch Platform Designer or Quartus.
$ErrorActionPreference = "Stop"
$ROOT  = "D:\altera_demo\chat_gpt_mlperf_demo"
$ENVC  = "$ROOT\scripts\ai_suite_env.cmd"
$ARCH  = "$ROOT\fpga\ai_suite\resnet8_agx3_int8_k16c8_ocp.arch"
$IR    = "$ROOT\build\fpga_ai\openvino_ir\resnet8_fpga_logits.xml"
$HET   = "$ROOT\build\fpga_ai\compile_k16c8_ocp"
$IPDIR = "$ROOT\build\fpga_ai\generated_ip"

function Run-Ai([string]$c, [string]$log) {
    & cmd.exe /d /c "call `"$ENVC`" >nul && $c" > $log 2>&1
    Write-Output "exit=$LASTEXITCODE  log=$log"
    if ($LASTEXITCODE -ne 0) { Get-Content $log -Tail 30; throw "failed: $c" }
}

Write-Output "=== STEP 1: hetero compile (transforms, ddr_buffer_info, on-chip param MIFs, area) ==="
Run-Ai ("dla_compiler --march `"$ARCH`" --network-file `"$IR`" " +
        "--fplugin HETERO:FPGA --fdisplay-device --foutput-format open_vino_hetero " +
        "--o resnet8_k16c8_ocp.aot --dumpdir `"$HET`" --overwrite-output-files " +
        "--fanalyze-performance --festimate-per-layer-latencies --fanalyze-area " +
        "--fdump-area-report area-report.txt") "$ROOT\build\fpga_ai\k16c8_ocp_hetero.log"

Write-Output "=== STEP 2: dla_create_ip with on-chip parameter ROMs ==="
$OCP = "$HET\TensorFlow_Lite_Frontend_IR\on_chip_parameters"
if (-not (Test-Path $OCP)) { throw "on_chip_parameters dir not produced: $OCP" }
Run-Ai ("dla_create_ip --skip-sim-env --overwrite --ip-dir `"$IPDIR`" " +
        "--arch `"$ARCH`" --on-chip-parameters-dir `"$OCP`" --unlicensed") `
       "$ROOT\build\fpga_ai\k16c8_ocp_createip.log"

Write-Output "=== generated architectures ==="
Get-ChildItem "$IPDIR\altera_ai_ip\verilog" -Directory | ForEach-Object { $_.Name }
