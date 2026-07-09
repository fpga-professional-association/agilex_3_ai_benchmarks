#!/usr/bin/env bash
# Lint the HyperBus controller RTL (strict) + build/run the BFM testbenches under Verilator
# (issue #13, plus hb_trainer for issue #14). Exits 0 only if the RTL lints clean AND every TB
# prints PASS.
set -euo pipefail
cd "$(dirname "$0")/../.."

RTL="rtl/hyperbus/hyperbus_pkg.sv rtl/hyperbus/hbmc_core.sv"
BFM="sim/hyperbus/w957d8nb_bfm.sv"
TB="sim/hyperbus/tb_hyperbus.sv"
INC="-Irtl/hyperbus"

echo "=== lint controller RTL (-Wall, strict) ==="
verilator --lint-only -Wall ${INC} ${RTL} --top-module hbmc_core
echo "RTL lint clean"

echo "=== build + run tb_hyperbus ==="
TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD \
            -Wno-PINCONNECTEMPTY -Wno-VARHIDDEN"
verilator --binary --timing -Wall ${TB_WAIVERS} -j 0 ${INC} --top-module tb_hyperbus \
  --Mdir sim/hyperbus/obj_tb ${RTL} ${BFM} ${TB} > sim/hyperbus/obj_tb.build.log 2>&1 || {
    echo "BUILD FAILED:"; cat sim/hyperbus/obj_tb.build.log; exit 1; }

out=$(sim/hyperbus/obj_tb/Vtb_hyperbus)
echo "${out}"
echo "${out}" | grep -q '^PASS$' || { echo "tb_hyperbus did not PASS"; exit 1; }
echo "HYPERBUS TB PASSED"

# ---- hb_trainer (issue #14) ----
# hb_trainer is a peer Avalon+CSR master to hbmc_core (not a child instance of it), so a strict lint
# of hb_trainer alone necessarily sees the rest of hyperbus_pkg's CSR_* constants as "unused from
# here" even though hbmc_core (linted above, with zero waivers) uses every one of them. That's a
# byproduct of sharing one canonical CSR-map package across sibling modules, not a real bug -- waived
# narrowly, only for this one lint invocation.
echo "=== lint hb_trainer RTL (-Wall, strict save one documented shared-package waiver) ==="
verilator --lint-only -Wall ${INC} ${RTL} rtl/hyperbus/hb_trainer.sv --top-module hb_trainer \
  -Wno-UNUSEDPARAM
echo "hb_trainer RTL lint clean"

echo "=== build + run tb_hb_trainer ==="
verilator --binary --timing -Wall ${TB_WAIVERS} -Wno-UNUSEDPARAM -Wno-BLKSEQ -j 0 ${INC} \
  --top-module tb_hb_trainer --Mdir sim/hyperbus/obj_tb_trainer \
  ${RTL} rtl/hyperbus/hb_trainer.sv ${BFM} sim/hyperbus/tb_hb_trainer.sv \
  > sim/hyperbus/obj_tb_trainer.build.log 2>&1 || {
    echo "BUILD FAILED:"; cat sim/hyperbus/obj_tb_trainer.build.log; exit 1; }

out=$(sim/hyperbus/obj_tb_trainer/Vtb_hb_trainer)
echo "${out}"
echo "${out}" | grep -q '^PASS$' || { echo "tb_hb_trainer did not PASS"; exit 1; }
echo "HB_TRAINER TB PASSED"
