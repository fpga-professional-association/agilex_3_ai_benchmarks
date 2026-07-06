#!/usr/bin/env bash
# Build + run the l1_pe_array testbench under Verilator (issue #11). Exits 0 only if the RTL lints
# clean (-Wall, strict) AND all four variant builds print PASS AND their checksums are identical
# (the reset-style x domain-split variants must be functionally equivalent — that equivalence is
# what makes the Quartus fmax deltas attributable to the timing discipline alone). See sim/README.md.
set -euo pipefail
cd "$(dirname "$0")/../.."

RTL="rtl/common/bench_pkg.sv rtl/common/cdc_bit_sync.sv rtl/common/pulse_sync.sv \
     rtl/common/async_fifo.sv \
     rtl/microbench/l0_tensor_chain/l0_lfsr.sv rtl/microbench/l0_tensor_chain/l0_mac_block.sv \
     rtl/microbench/l1_pe_array/l1_pe_cell.sv rtl/microbench/l1_pe_array/l1_pe_array.sv \
     rtl/microbench/l1_pe_array/l1_pe_core.sv rtl/microbench/l1_pe_array/l1_pe_top.sv"
INC="-Irtl/common -Irtl/microbench/l0_tensor_chain -Irtl/microbench/l1_pe_array"

echo "=== lint RTL (-Wall, strict) ==="
# -Wno-UNUSEDPARAM: bench_pkg.sv is shared; this top uses only the L1_ADDR_* section (same reason as
# sim/l0_tensor_chain/run.sh). Everything else stays strict.
verilator --lint-only -Wall -Wno-UNUSEDPARAM ${INC} ${RTL} --top-module l1_pe_top
echo "RTL lint clean"

TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD"
FLAGS="--binary --timing -Wall ${TB_WAIVERS} -j 0"
TB="sim/l1_pe_array/tb_l1_pe_array.sv"
OUTDIR="sim/l1_pe_array"

run_variant() {
  local heavy="$1" iso="$2"
  local tag="h${heavy}_i${iso}"
  local obj="obj_${tag}"
  echo "=== variant RESET_HEAVY=${heavy} ISOLATE=${iso} ==="
  verilator ${FLAGS} ${INC} --top-module tb_l1_pe_array --Mdir "${OUTDIR}/${obj}" \
    -GRESET_HEAVY=${heavy} -GISOLATE=${iso} \
    ${RTL} ${TB} > "${OUTDIR}/${obj}.build.log" 2>&1 || {
      echo "BUILD FAILED (${tag}):"; cat "${OUTDIR}/${obj}.build.log"; exit 1; }
  local out
  out=$("${OUTDIR}/${obj}/Vtb_l1_pe_array")
  echo "${out}"
  echo "${out}" | grep -q '^PASS$' || { echo "TB ${tag} did not PASS"; exit 1; }
  # extract checksum for the cross-variant equivalence check
  echo "${out}" | sed -n 's/.*checksum=\(0x[0-9a-fA-F]*\).*/\1/p'
}

CK_CM=$(run_variant 0 0 | tail -1)
CK_HM=$(run_variant 1 0 | tail -1)
CK_CI=$(run_variant 0 1 | tail -1)
CK_HI=$(run_variant 1 1 | tail -1)

echo "=== equivalence check ==="
echo "clean/merged=${CK_CM} heavy/merged=${CK_HM} clean/isolated=${CK_CI} heavy/isolated=${CK_HI}"
if [ "${CK_CM}" != "${CK_HM}" ] || [ "${CK_CM}" != "${CK_CI}" ] || [ "${CK_CM}" != "${CK_HI}" ]; then
  echo "FAIL: variant checksums differ — variants are NOT functionally equivalent"; exit 1
fi
if [ -z "${CK_CM}" ] || [ "${CK_CM}" = "0x00000000" ]; then
  echo "FAIL: checksum empty or zero"; exit 1
fi
echo "All four variants agree: checksum=${CK_CM}"
echo "ALL L1_PE_ARRAY TBS PASSED"
