#!/usr/bin/env bash
# Lint the PH3 wrapper (rtl/hyperbus/axc3000_hyperram_axi4.sv) + build/run its self-checking TB
# under Verilator, against the third_party/hyperram submodule's REAL hyperram_avalon (ctrl + PHY)
# and its golden device model. A PASS here proves the AXI4->Avalon bridge datapath through the real
# HyperBus IP (PHY_VARIANT="GENERIC"), not a stub -- PH3 blocker #1 CLOSED. See
# docs/ph3_submodule.md for the wiring and docs/ph3_status.md for what remains hardware-side.
#
# CRITICAL package-name collision (see docs/ph3_submodule.md): rtl/hyperbus/hyperbus_pkg.sv AND
# third_party/hyperram/rtl/hyperbus_pkg.sv both declare `package hyperbus_pkg` -- DIFFERENT
# packages, same name. This build compiles ONLY the submodule's copy and NEVER includes
# rtl/hyperbus/hyperbus_pkg.sv or rtl/hyperbus/hbmc_core.sv (the old stub datapath) in the same
# compilation unit as this TB.
#
# Exits 0 only if the wrapper lints clean AND the TB prints ALL AXC3000-HYPERRAM-AXI4 TBS PASSED.
set -euo pipefail
cd "$(dirname "$0")/../.."

HR="third_party/hyperram"

# ---- submodule sources (pinned; DO NOT edit) --------------------------------------------------
# Package FIRST. hyperram_avalon.sv is required for elaboration (axc3000_hyperram_axi4.sv
# instantiates it) even though it is not itself the compilation top; the vendor-only PHY skeletons
# (altera/xilinx) are intentionally NOT included -- PHY_VARIANT="GENERIC" in this TB means
# hyperbus_phy.sv's generate-if never selects those branches, so they need not elaborate.
SUB_SRCS=(
  "${HR}/rtl/hyperbus_pkg.sv"
  "${HR}/rtl/hyperbus_ctrl.sv"
  "${HR}/rtl/if/hyperbus_avalon.sv"
  "${HR}/rtl/phy/hyperbus_phy_generic.sv"
  "${HR}/rtl/phy/hyperbus_phy_sdr.sv"
  "${HR}/rtl/phy/hyperbus_phy.sv"
  "${HR}/rtl/hyperram_avalon.sv"
)

# ---- our RTL: bridge (unchanged) + the rewritten wrapper (split HyperBus pins) ----------------
WRAPPER_SRCS=(
  "rtl/hyperbus/axi4_hbmc_bridge.sv"
  "rtl/hyperbus/axc3000_hyperram_axi4.sv"
)

MODEL="${HR}/sim/model/hyperram_model.sv"
TB="sim/hyperbus/tb_axc3000_hyperram_axi4.sv"

INC="-I${HR}/rtl -I${HR}/rtl/if -I${HR}/rtl/phy -Irtl/hyperbus"

echo "=== lint wrapper RTL standalone (-Wall, strict; submodule sources on the command line so"
echo "    hyperram_avalon/phy elaborate) ==="
# -Wno-WIDTHEXPAND (2026-07-11, hyperram bump to b544bb7 / issue #13 fix-set retest): the submodule's
# own hyperbus_ctrl.sv (pinned; DO NOT edit) has pre-existing width mismatches unrelated to this
# wrapper's port wiring -- COMMIT_READ_MODE string-parameter comparisons (lines 619/628) and the new
# 4-bit dbg_lat_clocks/dbg_wr_lat_trim knobs used in 32-bit arithmetic (lines 1298/1321/1322). These
# now surface here too because u_ctrl only fully elaborates once its dbg_*/cal_* ports are actually
# connected (see this run's PINMISSING fix) -- already waived below for the TB build; mirrored here so
# the lint-only pass can reach a real pass/fail signal instead of aborting on submodule-internal noise.
LINT_WAIVERS="-Wno-TIMESCALEMOD -Wno-PINCONNECTEMPTY -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM \
              -Wno-DECLFILENAME -Wno-WIDTHEXPAND"
verilator --lint-only --timing -Wall ${LINT_WAIVERS} ${INC} "${SUB_SRCS[@]}" "${WRAPPER_SRCS[@]}" \
  --top-module axc3000_hyperram_axi4
echo "wrapper RTL lint clean"

echo "=== build + run tb_axc3000_hyperram_axi4 ==="
TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-WIDTHTRUNC -Wno-UNUSEDSIGNAL \
            -Wno-UNUSEDPARAM -Wno-TIMESCALEMOD -Wno-PINCONNECTEMPTY -Wno-VARHIDDEN \
            -Wno-DECLFILENAME"
verilator --binary --timing -Wall ${TB_WAIVERS} -j 0 ${INC} --top-module tb_axc3000_hyperram_axi4 \
  --Mdir sim/hyperbus/obj_axc3000_axi4 \
  "${SUB_SRCS[@]}" "${WRAPPER_SRCS[@]}" "${MODEL}" "${TB}" \
  > sim/hyperbus/obj_axc3000_axi4.build.log 2>&1 || {
    echo "BUILD FAILED:"; cat sim/hyperbus/obj_axc3000_axi4.build.log; exit 1; }

out=$(sim/hyperbus/obj_axc3000_axi4/Vtb_axc3000_hyperram_axi4)
echo "${out}"
echo "${out}" | grep -q '^ALL AXC3000-HYPERRAM-AXI4 TBS PASSED$' \
  || { echo "tb_axc3000_hyperram_axi4 did not PASS"; exit 1; }
echo "AXC3000-HYPERRAM-AXI4 TB PASSED"
