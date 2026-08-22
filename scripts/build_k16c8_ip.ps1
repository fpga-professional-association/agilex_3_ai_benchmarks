# Phase 12: compile the k16c8 INT8 arch and regenerate the CoreDLA IP.
# Does NOT touch Platform Designer or Quartus.
$ErrorActionPreference = "Stop"
$ROOT  = "D:\altera_demo\chat_gpt_mlperf_demo"
$ENVC  = "$ROOT\scripts\ai_suite_env.cmd"
$ARCH  = "$ROOT\fpga\ai_suite\resnet8_agx3_int8_k16c8.arch"
$IR    = "$ROOT\build\fpga_ai\openvino_ir\resnet8_fpga_logits.xml"
$HET   = "$ROOT\build\fpga_ai\compile_k16c8"
$CR    = "$ROOT\build\fpga_ai\compile_k16c8_cr"
$IPDIR = "$ROOT\build\fpga_ai\generated_ip"

function Run-Ai([string]$c, [string]$log) {
    & cmd.exe /d /c "call `"$ENVC`" >nul && $c" > $log 2>&1
    Write-Output "exit=$LASTEXITCODE  log=$log"
    if ($LASTEXITCODE -ne 0) { Get-Content $log -Tail 25; throw "failed: $c" }
}

Write-Output "=== STEP 1: hetero compile (transforms, ddr_buffer_info, area) ==="
Run-Ai ("dla_compiler --march `"$ARCH`" --network-file `"$IR`" " +
        "--fplugin HETERO:FPGA --fdisplay-device --foutput-format open_vino_hetero " +
        "--o resnet8_k16c8.aot --dumpdir `"$HET`" --overwrite-output-files " +
        "--fanalyze-performance --festimate-per-layer-latencies --fanalyze-area " +
        "--fdump-area-report area-report.txt") "$ROOT\build\fpga_ai\k16c8_hetero.log"

Write-Output "=== STEP 2: compiled_result blob (parameter image source) ==="
Run-Ai ("dla_compiler --march `"$ARCH`" --network-file `"$IR`" " +
        "--fplugin HETERO:FPGA --foutput-format dla_compiled_result " +
        "--o r8.bin --dumpdir `"$CR`" --overwrite-output-files") "$ROOT\build\fpga_ai\k16c8_cr.log"

Write-Output "=== STEP 3: dla_create_ip (evaluation / unlicensed) ==="
Run-Ai ("dla_create_ip --skip-sim-env --overwrite --ip-dir `"$IPDIR`" " +
        "--arch `"$ARCH`" --unlicensed") "$ROOT\build\fpga_ai\k16c8_createip.log"

Write-Output "=== generated architectures ==="
Get-ChildItem "$IPDIR\altera_ai_ip\verilog" -Directory | ForEach-Object { $_.Name }
