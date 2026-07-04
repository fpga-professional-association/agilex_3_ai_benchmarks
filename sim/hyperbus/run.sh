#!/usr/bin/env bash
# Lint the HyperBus controller RTL (strict) + build/run the BFM testbench under Verilator (issue #13).
# Exits 0 only if the RTL lints clean AND the TB prints PASS.
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
