#!/usr/bin/env bash
# Lint the L3 memtest/bandwidth engines (strict) + build/run their testbenches under Verilator
# (issue #14). Exits 0 only if the RTL lints clean AND every TB prints PASS.
set -euo pipefail
cd "$(dirname "$0")/../../.."

HB_RTL="rtl/hyperbus/hyperbus_pkg.sv rtl/hyperbus/hbmc_core.sv"
BFM="sim/hyperbus/w957d8nb_bfm.sv"
PKG="rtl/microbench/l3_memtest/l3_memtest_pkg.sv"
MT="rtl/microbench/l3_memtest/l3_memtest_engine.sv"
BW="rtl/microbench/l3_memtest/l3_bw_engine.sv"
INC="-Irtl/hyperbus -Irtl/microbench/l3_memtest"

# Both engines only reference their OWN CSR-map constants out of the shared l3_memtest_pkg (MT_* /
# BW_* respectively), so a strict standalone lint of one sees the other's constants as "unused from
# here" -- same documented, narrow waiver rationale as sim/hyperbus/run.sh's hb_trainer lint.
echo "=== lint l3_memtest_engine RTL (-Wall, strict save one documented shared-package waiver) ==="
verilator --lint-only -Wall ${INC} ${PKG} ${MT} --top-module l3_memtest_engine -Wno-UNUSEDPARAM
echo "l3_memtest_engine RTL lint clean"

echo "=== lint l3_bw_engine RTL (-Wall, strict save one documented shared-package waiver) ==="
verilator --lint-only -Wall ${INC} ${PKG} ${BW} --top-module l3_bw_engine -Wno-UNUSEDPARAM
echo "l3_bw_engine RTL lint clean"

TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD \
            -Wno-PINCONNECTEMPTY -Wno-VARHIDDEN -Wno-UNUSEDPARAM -Wno-BLKSEQ"

run_tb() {
  local top="$1"; shift
  local obj="obj_${top}"
  echo "=== ${top} ==="
  verilator --binary --timing -Wall ${TB_WAIVERS} -j 0 ${INC} --top-module "${top}" \
    --Mdir "sim/microbench/l3_memtest/${obj}" ${HB_RTL} ${PKG} "$@" ${BFM} \
    "sim/microbench/l3_memtest/${top}.sv" \
    > "sim/microbench/l3_memtest/${obj}.build.log" 2>&1 || {
      echo "BUILD FAILED for ${top}:"; cat "sim/microbench/l3_memtest/${obj}.build.log"; exit 1; }
  local out
  out=$("sim/microbench/l3_memtest/${obj}/V${top}")
  echo "${out}"
  echo "${out}" | grep -q '^PASS$' || { echo "TB ${top} did not PASS"; exit 1; }
}

run_tb tb_l3_memtest_engine ${MT}
run_tb tb_l3_bw_engine ${BW}

echo "ALL L3 MEMTEST/BANDWIDTH TBS PASSED"
