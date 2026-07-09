# rtl/coredla_hyperram/ — CoreDLA <-> HyperRAM glue (PH3)

Bridges the FPGA AI Suite CoreDLA IP's reduced-AXI4 "DDR" master port onto the
`third_party/hyperram` submodule's Avalon-MM HyperBus controller/PHY (`hyperram_avalon`), so the
AXC3000's on-board W957D8NB HyperRAM can stand in for the LPDDR4 EMIF CoreDLA normally targets.
See `docs/ph3_integration.md` (system swap), `docs/ph3_bridge_design.md` (bridge FSM/contract),
`docs/ph3_interfaces.md` (provenance), and `docs/ph3_submodule.md` (submodule wiring).

| Module | Role |
|---|---|
| `axi4_hbmc_bridge.sv` | Reduced AXI4 slave (CoreDLA DDR contract, DATA=256/ADDR=32) -> generic 16-bit-word Avalon-MM master (word-addressed, linear bursts). Single clock, single-outstanding, full-width writes only (partial WSTRB detected via `wstrb_partial_seen`, not read-modify-written). |
| `axc3000_hyperram_axi4.sv` | Wires the bridge's Avalon master 1:1 onto `hyperram_avalon`'s Avalon slave. SPLIT HyperBus pins (`hb_dq_o/oe/i`, `hb_rwds_o/oe/i`) — deliberately `inout`-free so it stays Verilator-clean and resolvable against a second bus driver (the golden device model) in sim. The Platform Designer component `quartus/ip/axc3000_hyperram_axi4/axc3000_hyperram_axi4_hw.tcl` points at this file. |
| `axc3000_hyperram_pads.sv` | Tiny synthesis-only wrapper: turns the SPLIT pins into real bidirectional `inout` HyperRAM package balls. Pure wiring, no logic/registers/reset. Instantiated by the PD component, a board `top.sv`, and `quartus/ph3_hyperram_char/`. |

## Not test infrastructure

Everything here is the production PH3 datapath and runs on the `third_party/hyperram` submodule's
real, silicon-proven HyperBus controller + PHY — not the earlier hand-rolled `hbmc_core`/
`hyperbus_pkg` datapath, which has been retired to `sim/replay/` as golden test infrastructure for
the record-replay integration TB (`sim/replay/tb_replay_integ.sv`). See that directory's module
headers and `docs/ph3_submodule.md` for why the swap happened.

## Verification

`sim/hyperbus/run_hyperram_axi4.sh` (Verilator): lints `axc3000_hyperram_axi4.sv` and builds/runs
`sim/hyperbus/tb_axc3000_hyperram_axi4.sv` against the submodule's real `hyperram_avalon`
(`PHY_VARIANT="GENERIC"`) and its golden device model — AXI4 INCR write bursts followed by
byte-exact read-back, `bresp`/`rresp` checks, a WSTRB-partial trip-wire case.
