#!/usr/bin/env bash
# Lint the replay RTL (strict) + build/run the record-replay testbenches under Verilator (issue #16).
# Exits 0 only if the RTL lints clean AND every TB prints PASS. Uses the committed fixture
# (fixtures/records.hex + fixture_params.svh); regenerate with `python sim/replay/gen_fixture.py`.
set -euo pipefail
cd "$(dirname "$0")/../.."

RTL="rtl/common/sync_fifo.sv rtl/replay/pingpong_buf.sv rtl/replay/record_framer.sv rtl/replay/replay_top.sv"
INC="-Irtl/common -Irtl/replay -Irtl/hyperbus -Irtl/scoreboard -Isim/replay/fixtures"
WAIV="-Wno-fatal -Wno-INITIALDLY -Wno-WIDTHEXPAND -Wno-UNUSEDSIGNAL -Wno-TIMESCALEMOD -Wno-VARHIDDEN -Wno-PINCONNECTEMPTY"

echo "=== lint replay RTL (-Wall, strict) ==="
verilator --lint-only -Wall -Irtl/common -Irtl/replay ${RTL} --top-module replay_top
echo "RTL lint clean"

build_run() {  # <name> <mdir> <extra verilator args...> -- <files...>
  local name="$1" mdir="$2"; shift 2
  local args=(); local files=()
  local seen=0
  for a in "$@"; do if [ "$a" = "--" ]; then seen=1; continue; fi
    if [ $seen -eq 0 ]; then args+=("$a"); else files+=("$a"); fi; done
  echo "=== ${name} ==="
  verilator --binary --timing -Wall ${WAIV} -j 0 ${INC} --top-module "${name%%:*}" --Mdir "${mdir}" \
    "${args[@]}" "${files[@]}" > "${mdir}.log" 2>&1 || { echo "BUILD FAILED"; cat "${mdir}.log"; exit 1; }
  local out; out=$("${mdir}/V${name%%:*}")
  echo "${out}"; echo "${out}" | grep -q '^PASS$' || { echo "${name} did not PASS"; exit 1; }
}

# 1) ping-pong mode
build_run tb_replay sim/replay/obj_pp -- \
  ${RTL} sim/replay/avalon_mem_bfm.sv sim/replay/tb_replay.sv
# 2) cut-through mode (record larger than the FIFO)
build_run tb_replay sim/replay/obj_ct -GCT=1 -- \
  ${RTL} sim/replay/avalon_mem_bfm.sv sim/replay/tb_replay.sv
# 3) integration: replay + real HyperBus controller + device + scoreboard, 100 records
build_run tb_replay_integ sim/replay/obj_integ -- \
  rtl/common/bench_pkg.sv rtl/common/cdc_bit_sync.sv rtl/common/async_fifo.sv rtl/common/sync_fifo.sv \
  rtl/hyperbus/hyperbus_pkg.sv rtl/hyperbus/hbmc_core.sv sim/hyperbus/w957d8nb_bfm.sv \
  rtl/scoreboard/sb_frontend.sv rtl/scoreboard/scoreboard.sv \
  rtl/replay/pingpong_buf.sv rtl/replay/record_framer.sv rtl/replay/replay_top.sv \
  sim/replay/tb_replay_integ.sv

echo "ALL REPLAY TBS PASSED"
