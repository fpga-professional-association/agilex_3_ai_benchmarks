#!/usr/bin/env bash
# Lint the L2 M20K aggregate-bandwidth microbench (strict) + build/run its testbench under
# Verilator across the three PLAN §7 L2 / issue #12 configs. Exits 0 only if the RTL lints clean
# AND every config's TB prints PASS.
set -euo pipefail
cd "$(dirname "$0")/../../.."

PKG="rtl/microbench/l2_m20k_bw/m20k_bw_pkg.sv"
BANK="rtl/microbench/l2_m20k_bw/m20k_bw_bank.sv"
TOP="rtl/microbench/l2_m20k_bw/m20k_bw.sv"
TB="sim/microbench/l2_m20k_bw/tb_m20k_bw.sv"
OUTDIR="sim/microbench/l2_m20k_bw"
INC="-Irtl/microbench/l2_m20k_bw"

echo "=== lint m20k_bw RTL (-Wall, strict) ==="
verilator --lint-only -Wall ${INC} ${PKG} ${BANK} ${TOP} --top-module m20k_bw
echo "RTL lint clean"

TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL \
            -Wno-TIMESCALEMOD -Wno-VARHIDDEN -Wno-BLKSEQ -Wno-DECLFILENAME"
FLAGS="--binary --timing -Wall ${TB_WAIVERS} -j 0"

# GEOMETRY encodes as m20k_bw_pkg::GEOM_BANKED=0 / GEOM_SHARED=1. Matches issue #12's three named
# configs (a/b/c below) plus one extra point (d) for full 2x2 coverage of the two independent knobs:
#   a) banked one-port-per-reader, output registers on   (good geometry)
#   b) shared single port, round-robin, output registers on (serialized anti-pattern)
#   c) banked, output registers off                       (fmax-cost geometry)
#   d) shared, output registers off                        (extra coverage point)

run_variant() {
  local tag="$1" geom="$2" outreg="$3"
  local obj="obj_${tag}"
  echo "=== ${tag}: GEOMETRY=${geom} OUTPUT_REG=${outreg} ==="
  verilator ${FLAGS} ${INC} --top-module tb_m20k_bw --Mdir "${OUTDIR}/${obj}" \
    -GGEOMETRY=${geom} -GOUTPUT_REG=${outreg} \
    ${PKG} ${BANK} ${TOP} ${TB} > "${OUTDIR}/${obj}.build.log" 2>&1 || {
      echo "BUILD FAILED (${tag}):"; cat "${OUTDIR}/${obj}.build.log"; exit 1; }
  local out
  out=$("${OUTDIR}/${obj}/Vtb_m20k_bw")
  echo "${out}"
  echo "${out}" | grep -q '^PASS$' || { echo "TB ${tag} did not PASS"; exit 1; }
}

# a) banked, output registers on  (GEOM_BANKED=0)
run_variant "a_banked_outreg" 0 1
# b) shared round-robin, output registers on  (GEOM_SHARED=1)
run_variant "b_shared_roundrobin" 1 1
# c) banked, output registers off  (GEOM_BANKED=0)
run_variant "c_banked_noreg" 0 0
# extra: shared + no output reg, for full 2x2 coverage of the two independent knobs
run_variant "d_shared_noreg" 1 0

echo "ALL L2_M20K_BW TBS PASSED"
