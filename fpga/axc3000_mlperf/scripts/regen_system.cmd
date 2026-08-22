@echo off
REM ---------------------------------------------------------------------------
REM Phase 10 Platform Designer regeneration.
REM
REM The checked-in NIOSV_lab.qsys cannot be generated on its own: its cached
REM footprint for the fpga_ai instance is stale relative to
REM ip/NIOSV_lab/NIOSV_lab_fpga_ai_*.ip, so qsys-generate reports "fpga_ai
REM declares port ... which is missing in file".  That is true of the pristine
REM Phase 9 file too -- it is not damage introduced by Phase 10.  The proven
REM flow (build_fpga_ai_rtl.ps1) always re-runs rebuild_fpga_ai_instance.tcl
REM first, which removes and re-adds the instance from the live altera_ai_ip
REM component.  We replicate exactly that, then apply the Phase 10 clock split
REM on top, then generate.
REM
REM We deliberately do NOT run build_fpga_ai_rtl.ps1 itself: that script
REM re-runs dla_compiler/dla_create_ip against the older
REM resnet8_agx3_logits.arch, which would replace the Phase 9 vendor
REM V1_8x8_AGX3 IP.  The arch, graph and IP must not change this phase.
REM ---------------------------------------------------------------------------
setlocal
set "REPO=D:\altera_demo\chat_gpt_mlperf_demo"
call "%REPO%\scripts\ai_suite_env.cmd" || exit /b 1
cd /d "%REPO%\fpga\axc3000_mlperf" || exit /b 1
set "SP=%REPO%\build\fpga_ai\generated_ip,%REPO%\fpga\axc3000_mlperf\ip\resnet8_stream_bridge,$"

echo === STEP 1: rebuild fpga_ai instance (V1_8x8_AGX3 + pseudo-DDR RAMs) ===
qsys-script.exe --system-file=NIOSV_lab.qsys --quartus-project=axc3000_top --search-path="%SP%" --script=scripts/rebuild_fpga_ai_instance.tcl
if errorlevel 1 exit /b 1

echo === STEP 2: split dla_clk onto iopll_0.outclk1 ===
qsys-script.exe --system-file=NIOSV_lab.qsys --quartus-project=axc3000_top --search-path="%SP%" --script=scripts/split_dla_clock.tcl
if errorlevel 1 exit /b 1

echo === STEP 3: generate synthesis RTL ===
qsys-generate.exe NIOSV_lab.qsys --synthesis=VERILOG --quartus-project=axc3000_top --search-path="%SP%" --parallel=off
exit /b %ERRORLEVEL%
