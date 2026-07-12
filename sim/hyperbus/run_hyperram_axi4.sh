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

# ---- boundary-alias regression (2026-07-11, silicon 4KB-alias retest): tb_axc3000_hyperram_axi4
# above drives axc3000_hyperram_axi4.sv (the "SPLIT_PHY" branch) with BURST_BOUNDARY_WORDS=0 /
# WR_COALESCE=0 (hyperram_avalon's own defaults, never overridden by that wrapper) -- i.e. the
# issue-13 ROUND 3/4 row-boundary machinery (dbg_end_cwrite/dbg_spray_defuse, gated by
# `end_cwrite_aligned = (BURST_BOUNDARY_WORDS != 0) && ...`) is completely INERT there. The REAL
# board build elaborates axc3000_hyperram_pads.sv's default IO_VARIANT="DDIO_GPIO" branch, which
# sets BURST_BOUNDARY_WORDS=1024 / WR_COALESCE=1 (its own CTRL_* constants) -- a materially
# different configuration of the SAME shared hyperbus_avalon/hyperbus_ctrl RTL. tb_hyperbus_
# boundary_alias.sv instantiates hyperram_avalon directly with that exact parameter set (PHY_
# VARIANT="GENERIC" swapped in for the real device-primitive hyperbus_gpio_io, which is not
# Verilator-simulable) and reproduces the on-silicon page-alias reproduction shape (write ALL
# pages first, THEN read ALL pages back) at the real symptom's sentinel addresses. See
# docs/coredla_hyperram_hang_diagnosis.md and scratch/hyperram_retest/alias_diagnosis.md for the
# on-board symptom this is chasing.
echo "=== build + run tb_hyperbus_boundary_alias ==="
verilator --binary --timing -Wall ${TB_WAIVERS} -j 0 ${INC} --top-module tb_hyperbus_boundary_alias \
  --Mdir sim/hyperbus/obj_boundary_alias \
  "${SUB_SRCS[@]}" "${MODEL}" "sim/hyperbus/tb_hyperbus_boundary_alias.sv" \
  > sim/hyperbus/obj_boundary_alias.build.log 2>&1 || {
    echo "BUILD FAILED:"; cat sim/hyperbus/obj_boundary_alias.build.log; exit 1; }

out2=$(sim/hyperbus/obj_boundary_alias/Vtb_hyperbus_boundary_alias)
echo "${out2}"
echo "${out2}" | grep -q '^ALL HYPERBUS-BOUNDARY-ALIAS TBS PASSED$' \
  || { echo "tb_hyperbus_boundary_alias did not PASS"; exit 1; }
echo "HYPERBUS-BOUNDARY-ALIAS TB PASSED"

# ---- per-fit calibration CSR (2026-07-11): rtl/hyperbus/hyperram_cal_csr.sv is the new Avalon-MM
# slave that ports the submodule bench's REG_DBG/REG_CAL runtime knobs into the ED (wired into
# axc3000_hyperram_pads.sv's DDIO_GPIO branch, exported through the PD component + ed_zero.tcl at
# base 0x9000_0000). It is pure logic (no device primitives), so it lints + self-checks under
# Verilator here. -Wno-UNUSEDSIGNAL: REG_DBG[8] is the cr0-reprog W1 strobe (never stored, reads 0),
# so bit 8 of the r_dbg store is intentionally unused. ----
echo "=== lint hyperram_cal_csr standalone ==="
verilator --lint-only --timing -Wall -Wno-DECLFILENAME -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD \
  rtl/hyperbus/hyperram_cal_csr.sv --top-module hyperram_cal_csr
echo "hyperram_cal_csr lint clean"

echo "=== build + run tb_hyperram_cal_csr ==="
verilator --binary --timing -Wall ${TB_WAIVERS} -j 0 -Irtl/hyperbus --top-module tb_hyperram_cal_csr \
  --Mdir sim/hyperbus/obj_cal_csr \
  rtl/hyperbus/hyperram_cal_csr.sv sim/hyperbus/tb_hyperram_cal_csr.sv \
  > sim/hyperbus/obj_cal_csr.build.log 2>&1 || {
    echo "BUILD FAILED:"; cat sim/hyperbus/obj_cal_csr.build.log; exit 1; }

out3=$(sim/hyperbus/obj_cal_csr/Vtb_hyperram_cal_csr)
echo "${out3}"
echo "${out3}" | grep -q '^ALL HYPERRAM-CAL-CSR TBS PASSED$' \
  || { echo "tb_hyperram_cal_csr did not PASS"; exit 1; }
echo "HYPERRAM-CAL-CSR TB PASSED"
