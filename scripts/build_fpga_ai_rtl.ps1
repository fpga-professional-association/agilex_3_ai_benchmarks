[CmdletBinding()]
param(
    [switch]$CompileOnly,
    [switch]$Evaluation,
    [switch]$SkipQuartus,
    [string]$QuartusRoot = "C:\altera_pro\26.1\quartus"
)

$ErrorActionPreference = "Stop"
$sw = [Diagnostics.Stopwatch]::StartNew()
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$envScript = Join-Path $PSScriptRoot "ai_suite_env.cmd"
$model = Join-Path $repoRoot "third_party\mlcommons_tiny\benchmark\training\image_classification\trained_models\pretrainedResnet_quant.tflite"
$rawIr = Join-Path $repoRoot "build\fpga_ai\openvino_ir\resnet8.xml"
$adaptedIr = Join-Path $repoRoot "build\fpga_ai\openvino_ir\resnet8_fpga_logits.xml"
$compileDir = Join-Path $repoRoot "build\fpga_ai\compile_logits_k4"
$parameterDir = Join-Path $compileDir "TensorFlow_Lite_Frontend_IR\on_chip_parameters"
$arch = Join-Path $repoRoot "fpga\ai_suite\resnet8_agx3_logits.arch"
$ipDir = Join-Path $repoRoot "build\fpga_ai\generated_ip"
$qpf = Join-Path $repoRoot "fpga\axc3000_mlperf\axc3000_top.qpf"
$qsys = Join-Path $repoRoot "fpga\axc3000_mlperf\NIOSV_lab.qsys"
$qsysScript = Join-Path $repoRoot "fpga\axc3000_mlperf\scripts\rebuild_fpga_ai_instance.tcl"
$bridgeDir = Join-Path $repoRoot "fpga\axc3000_mlperf\ip\resnet8_stream_bridge"

$expectedModel = "3C002613D1B2475EB51DD78DFB85A546C8AE658DEE71CF6ADE43B022FE205415"
$expectedXml = "C104ECDC1E0C1777B0071D51E14119BD6B280CE1B21752FA1F84AAB93DCD4A81"
$expectedBin = "519DE0A6211125C394F92FCACF5E6F4664B1DA6C9D2B0280B1BD913A0077364B"

function Assert-Hash([string]$Path, [string]$Expected) {
    if (-not (Test-Path -LiteralPath $Path)) { throw "Missing required file: $Path" }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    if ($actual -ne $Expected) { throw "SHA-256 mismatch for $Path`: expected $Expected, got $actual" }
}

function Run-Tool([string]$Exe, [string[]]$Arguments) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe failed with exit code $LASTEXITCODE" }
}

function Run-AiCommand([string]$Command) {
    & cmd.exe /d /c "call `"$envScript`" >nul && $Command"
    if ($LASTEXITCODE -ne 0) { throw "FPGA AI Suite command failed with exit code $LASTEXITCODE" }
}

Assert-Hash $model $expectedModel
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $rawIr) | Out-Null

Run-AiCommand "python `"$repoRoot\tools\convert_tflite_to_openvino.py`" `"$model`" `"$rawIr`""
Run-AiCommand "python `"$repoRoot\tools\prepare_resnet8_openvino.py`" `"$rawIr`" `"$adaptedIr`" --no-softmax --validate"
Assert-Hash $adaptedIr $expectedXml
Assert-Hash ([IO.Path]::ChangeExtension($adaptedIr, ".bin")) $expectedBin

Run-AiCommand (
    "dla_compiler --march `"$arch`" --network-file `"$adaptedIr`" " +
    "--fplugin HETERO:FPGA --fdisplay-device --foutput-format open_vino_hetero " +
    "--o resnet8_logits.aot --dumpdir `"$compileDir`" --overwrite-output-files " +
    "--fanalyze-performance --festimate-per-layer-latencies --fanalyze-area " +
    "--fdump-area-report area-report.txt"
)

if ($CompileOnly) {
    $sw.Stop()
    Write-Host ("compile_only_wall_seconds={0:R}" -f $sw.Elapsed.TotalSeconds)
    exit 0
}

# Evaluation hardware is valid for the first 10,000 inferences.  Make that
# choice explicit; production builds still fail closed when CoreDLA is absent.
$licenseArgument = ""
if ($Evaluation) {
    $licenseArgument = " --unlicensed"
    Write-Host "ip_license_mode=evaluation_10000_inferences"
} else {
    & (Join-Path $PSScriptRoot "check_coredla_license.ps1") -QuartusRoot $QuartusRoot
    $licenseArgument = " --licensed"
    Write-Host "ip_license_mode=production"
}

Run-AiCommand (
    "dla_create_ip --skip-sim-env --overwrite --ip-dir `"$ipDir`" " +
    "--arch `"$arch`" --model `"$adaptedIr`" " +
    "--on-chip-parameters-dir `"$parameterDir`"$licenseArgument"
)

if (-not $SkipQuartus) {
    $qsysScriptExe = Join-Path $QuartusRoot "sopc_builder\bin\qsys-script.exe"
    $qsysGenerateExe = Join-Path $QuartusRoot "sopc_builder\bin\qsys-generate.exe"
    $quartusSh = Join-Path $QuartusRoot "bin64\quartus_sh.exe"
    $searchPath = "$ipDir,$bridgeDir,`$"
    $qsysDirectory = Split-Path -Parent $qsys
    $qsysFileName = Split-Path -Leaf $qsys

    Push-Location $qsysDirectory
    try {
        # qsys-script 26.1 on Windows interprets an absolute system path as an
        # HDL entity name.  Run in the project directory with a leaf filename.
        Run-Tool $qsysScriptExe @(
            "--system-file=$qsysFileName", "--quartus-project=$qpf",
            "--search-path=$searchPath", "--script=$qsysScript"
        )
        Run-Tool $qsysGenerateExe @(
            $qsysFileName, "--synthesis=VERILOG", "--quartus-project=$qpf",
            "--search-path=$searchPath", "--parallel=off"
        )
    } finally {
        Pop-Location
    }
    Run-Tool $quartusSh @("--flow", "compile", $qpf)
}

$sw.Stop()
Write-Host ("build_wall_seconds={0:R}" -f $sw.Elapsed.TotalSeconds)
