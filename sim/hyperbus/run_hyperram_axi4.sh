#!/usr/bin/env bash
# Lint the PH3 wrapper (rtl/coredla_hyperram/axc3000_hyperram_axi4.sv) + build/run its self-checking
# TB under Verilator, against the third_party/hyperram submodule's REAL hyperram_avalon (ctrl + PHY)
# and its golden device model. A PASS here proves the AXI4->Avalon bridge datapath through the real
# HyperBus IP (PHY_VARIANT="GENERIC"), not a stub -- PH3 blocker #1 CLOSED. See
# docs/ph3_submodule.md for the wiring and docs/ph3_status.md for what remains hardware-side.
#
# Package-name note (see docs/ph3_submodule.md): third_party/hyperram/rtl/hyperbus_pkg.sv (the
# submodule's package) used to collide by name with an in-repo rtl/hyperbus/hyperbus_pkg.sv; that
# in-repo package has since been renamed to hbmc_pkg and relocated to sim/replay/ (test
# infrastructure for the record-replay integration TB) precisely to remove the collision. This
# build never includes sim/replay/hbmc_pkg.sv or sim/replay/hbmc_core.sv (the old stub datapath) in
# the same compilation unit as this TB -- only the submodule's copy.
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
  "rtl/coredla_hyperram/axi4_hbmc_bridge.sv"
  "rtl/coredla_hyperram/axc3000_hyperram_axi4.sv"
)

MODEL="${HR}/sim/model/hyperram_model.sv"
TB="sim/hyperbus/tb_axc3000_hyperram_axi4.sv"

INC="-I${HR}/rtl -I${HR}/rtl/if -I${HR}/rtl/phy -Irtl/coredla_hyperram"

echo "=== lint wrapper RTL standalone (-Wall, strict; submodule sources on the command line so"
echo "    hyperram_avalon/phy elaborate) ==="
LINT_WAIVERS="-Wno-TIMESCALEMOD -Wno-PINCONNECTEMPTY -Wno-UNUSEDSIGNAL -Wno-UNUSEDPARAM \
              -Wno-DECLFILENAME"
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
