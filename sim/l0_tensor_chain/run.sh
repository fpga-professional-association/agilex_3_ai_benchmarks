#!/usr/bin/env bash
# Build + run the l0_tensor_chain testbench under Verilator (issue #9). Exits 0 only if the RTL
# lints clean (-Wall, strict) AND the TB prints PASS. See sim/README.md.
set -euo pipefail
cd "$(dirname "$0")/../.."

RTL_COMMON="rtl/common/bench_pkg.sv"
L0="rtl/microbench/l0_tensor_chain/l0_lfsr.sv rtl/microbench/l0_tensor_chain/l0_mac_block.sv \
    rtl/microbench/l0_tensor_chain/l0_tensor_chain.sv"
INC="-Irtl/common -Irtl/microbench/l0_tensor_chain"

echo "=== lint RTL (-Wall, strict) ==="
# -Wno-UNUSEDPARAM: bench_pkg.sv is shared with rtl/scoreboard/ (issue #15); this module only uses
# the L0_ADDR_* section of it, so linting bench_pkg.sv's other (scoreboard-only) params against
# THIS top-module alone would otherwise flag them as unused. Everything else stays strict.
verilator --lint-only -Wall -Wno-UNUSEDPARAM ${INC} ${RTL_COMMON} ${L0} --top-module l0_tensor_chain
echo "RTL lint clean"

TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD"
FLAGS="--binary --timing -Wall ${TB_WAIVERS} -j 0"

run_tb() {
  local top="$1"; shift
  local obj="obj_${top}"
  echo "=== ${top} ==="
  verilator ${FLAGS} ${INC} --top-module "${top}" --Mdir "sim/l0_tensor_chain/${obj}" \
    ${RTL_COMMON} ${L0} "$@" > "sim/l0_tensor_chain/${obj}.build.log" 2>&1 || {
      echo "BUILD FAILED for ${top}:"; cat "sim/l0_tensor_chain/${obj}.build.log"; exit 1; }
  local out
  out=$("sim/l0_tensor_chain/${obj}/V${top}")
  echo "${out}"
  echo "${out}" | grep -q '^PASS$' || { echo "TB ${top} did not PASS"; exit 1; }
}

run_tb tb_l0_tensor_chain sim/l0_tensor_chain/tb_l0_tensor_chain.sv

echo "ALL L0_TENSOR_CHAIN TBS PASSED"
