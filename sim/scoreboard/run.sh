#!/usr/bin/env bash
# Build + run the scoreboard testbenches under Verilator (issue #15).
# Exits 0 only if the RTL lints clean (-Wall, strict) AND every TB prints PASS. See sim/README.md.
set -euo pipefail
cd "$(dirname "$0")/../.."

RTL_COMMON="rtl/common/bench_pkg.sv rtl/common/cdc_bit_sync.sv rtl/common/pulse_sync.sv \
            rtl/common/async_fifo.sv rtl/common/sync_fifo.sv"
SB="rtl/scoreboard/sb_frontend.sv rtl/scoreboard/scoreboard.sv rtl/scoreboard/result_log_writer.sv"
INC="-Irtl/common -Irtl/scoreboard"

# 1) Strict lint of the synthesizable RTL — no warnings tolerated.
echo "=== lint RTL (-Wall, strict) ==="
verilator --lint-only -Wall ${INC} ${RTL_COMMON} ${SB} --top-module scoreboard
verilator --lint-only -Wall ${INC} rtl/common/sync_fifo.sv rtl/scoreboard/result_log_writer.sv \
  --top-module result_log_writer
echo "RTL lint clean"

# 2) Build + run each TB. TB-only warning classes are relaxed (testbench idioms), but the RTL above
#    was already linted strictly, so real RTL issues still fail the run.
TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD"
FLAGS="--binary --timing -Wall ${TB_WAIVERS} -j 0"

run_tb() {
  local top="$1"; shift
  local obj="obj_${top}"
  echo "=== ${top} ==="
  verilator ${FLAGS} ${INC} --top-module "${top}" --Mdir "sim/scoreboard/${obj}" \
    ${RTL_COMMON} ${SB} "$@" > "sim/scoreboard/${obj}.build.log" 2>&1 || {
      echo "BUILD FAILED for ${top}:"; cat "sim/scoreboard/${obj}.build.log"; exit 1; }
  local out
  out=$("sim/scoreboard/${obj}/V${top}")
  echo "${out}"
  echo "${out}" | grep -q '^PASS$' || { echo "TB ${top} did not PASS"; exit 1; }
}

run_tb tb_scoreboard  sim/scoreboard/tb_scoreboard.sv
run_tb tb_argmax      sim/scoreboard/tb_argmax.sv
run_tb tb_result_log  sim/scoreboard/tb_result_log.sv

echo "ALL SCOREBOARD TBS PASSED"
