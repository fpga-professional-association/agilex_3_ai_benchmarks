#!/usr/bin/env bash
# Lint the AXI4->HyperRAM bridge RTL (strict) + build/run its self-checking TB under Verilator (PH3).
# The TB drives the REAL hbmc_core + W957D8NB BFM, so a PASS proves the bridge datapath end to end.
# Exits 0 only if the RTL lints clean AND the TB prints ALL AXI4-HBMC BRIDGE TBS PASSED.
set -euo pipefail
cd "$(dirname "$0")/../.."

BRIDGE="rtl/hyperbus/axi4_hbmc_bridge.sv"
RTL="rtl/hyperbus/hyperbus_pkg.sv rtl/hyperbus/hbmc_core.sv ${BRIDGE}"
BFM="sim/hyperbus/w957d8nb_bfm.sv"
TB="sim/hyperbus/tb_axi4_hbmc_bridge.sv"
INC="-Irtl/hyperbus"

echo "=== lint bridge RTL (-Wall, strict) ==="
verilator --lint-only -Wall ${INC} ${BRIDGE} --top-module axi4_hbmc_bridge
echo "bridge RTL lint clean"

echo "=== build + run tb_axi4_hbmc_bridge ==="
TB_WAIVERS="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD \
            -Wno-PINCONNECTEMPTY -Wno-VARHIDDEN"
verilator --binary --timing -Wall ${TB_WAIVERS} -j 0 ${INC} --top-module tb_axi4_hbmc_bridge \
  --Mdir sim/hyperbus/obj_bridge ${RTL} ${BFM} ${TB} > sim/hyperbus/obj_bridge.build.log 2>&1 || {
    echo "BUILD FAILED:"; cat sim/hyperbus/obj_bridge.build.log; exit 1; }

out=$(sim/hyperbus/obj_bridge/Vtb_axi4_hbmc_bridge)
echo "${out}"
echo "${out}" | grep -q '^ALL AXI4-HBMC BRIDGE TBS PASSED$' \
  || { echo "tb_axi4_hbmc_bridge did not PASS"; exit 1; }
echo "AXI4-HBMC BRIDGE TB PASSED"
