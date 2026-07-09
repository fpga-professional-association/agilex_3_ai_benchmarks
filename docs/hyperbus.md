# HyperBus / HyperRAM controller (issue #13)

Controller + behavioral device model for the Winbond **W957D8NB** (128 Mb = 16 MB, ×8 HyperBus DDR,
1.8 V) on the AXC3000. PLAN §4 flags this as the single biggest infrastructure gap: Quartus ships no
HyperBus controller and the stock Nios V example runs from internal RAM only.

> **Superseded for the production PH3 datapath (see `docs/ph3_submodule.md`).** PH3 replaced this
> clean-room controller with the `third_party/hyperram` submodule's silicon-proven controller + PHY
> (`rtl/coredla_hyperram/`). The `hbmc_core`/`hyperbus_pkg` design documented below (renamed
> `hbmc_pkg` during the CoreDLA-HyperRAM rename cleanup) still exists, but only as golden test
> infrastructure at `sim/replay/{hbmc_core.sv,hbmc_pkg.sv,w957d8nb_bfm.sv}` for the record-replay
> integration TB (`sim/replay/tb_replay_integ.sv`) — its standalone Quartus/TB path described in
> "Hardware handoff" below (`quartus/hyperbus_smoke/`, `sim/hyperbus/tb_hyperbus.sv`,
> `sim/hyperbus/run.sh`) has been removed as dead weight; this doc's protocol-model and CSR-map
> content otherwise still accurately describes `sim/replay/hbmc_core.sv`.

## Provenance / decision record

Step 1 of the issue was to survey existing cores before writing RTL. Outcome:

- **OpenHBMC** (Apache-2.0), **PULP `hyperbus`** (SolderPad/Apache), **LiteHyperBus** (BSD) are all
  viable open cores. They are AXI/OBI/Wishbone-native and carry vendor-specific DDR-IO PHYs (mostly
  Xilinx/Lattice), so adopting one means porting its PHY to Agilex DDR-IO **and** wrapping its bus to
  Avalon-MM regardless.
- For this benchmark the memory access pattern is narrow: linear bursts for record replay (#16) and
  big-model weights, at a conservative 100 MHz first. A **clean-room minimal controller** sized to
  that need is easier to own, to timing-close, and — critically — to simulate end-to-end here without
  pulling a vendor PHY into the protocol layer.

**Decision:** implement a minimal clean-room Avalon-MM HyperBus controller (now `sim/replay/hbmc_core.sv`,
test infrastructure only — see the status note above),
keeping the Agilex-specific DDR-IO in a separate PHY (synthesis only). If we later need trained
capture at 166/200 MHz beyond what a simple PHY gives (#14), revisit OpenHBMC's PHY as a reference.
License note: no third-party RTL is vendored here, so there is no license to carry.

**Speed grade:** the exact W957D8NB part marking / datasheet rev must be read off the board before
committing to 166 vs 200 MHz (PLAN §4: a 20 % swing on every memory number). This issue targets
100 MHz (200 MB/s peak) only; pushing the clock with trained capture is #14.

## Protocol model (simulation)

The 8-bit DDR bus is modeled at **byte-per-beat** granularity (one DQ byte per sim clock — an SDR
abstraction of the real DDR edges). This is protocol-accurate — 6-beat CA, latency counted in beats,
RWDS-strobed reads, RWDS-masked writes, variable-latency doubling, and a mid-burst row-crossing gap —
but **not AC-timing-accurate**. Real datasheet timing is closed by the PHY + `.sdc`, not the sim
(issue #13 explicitly scopes it this way).

Alignment: the controller drives `cs_n` combinationally and both the controller and the BFM derive
their beat counter from it (`beat <= cs_n ? 0 : beat+1`), so they stay in exact lockstep. See
`sim/replay/hbmc_core.sv` and `sim/replay/w957d8nb_bfm.sv`.

Originally verified standalone by `sim/hyperbus/tb_hyperbus.sv` (Verilator, `sim/hyperbus/run.sh`):
device-register (ID) read, single word R/W, linear burst R/W crossing a row boundary, and fixed vs
variable latency with and without a refresh collision. That standalone TB has since been removed
(dead weight once PH3 retired this controller to test infrastructure); the same controller is now
exercised only as part of `sim/replay/tb_replay_integ.sv` (`sim/replay/run.sh`).

## CSR map (`hbmc_core`, 32-bit registers)

| Offset | Register | Function |
|---|---|---|
| 0x00 | CONFIG | bit0 `fixed_latency` (must match the device CR0 mode) |
| 0x04 | LATENCY | base latency in beats (host sets to match the device) |
| 0x08 | CAPDELAY | capture-delay taps to the PHY — **the #14 training hook** (stored; drives IO delay in HW) |
| 0x0C | STATUS | bit0 busy |
| 0x10 | DEV_ADDR | device register-space address (ID0/ID1/CR0/CR1) |
| 0x14 | DEV_WDAT | device register write data |
| 0x18 | DEV_CTRL | bit0 GO (self-clearing) · bit1 RW (1=read) — triggers a register-space transaction |
| 0x1C | DEV_RDAT | device register read data (valid after GO completes) |

Data path: a separate Avalon-MM 16-bit slave (`av_*`), word-addressed, linear bursts. Reads emit
`readdatavalid` per word; writes accept a word per `!waitrequest` beat and mask stall beats via RWDS.

## Latency model

`effective = register_write ? 0 : fixed ? base : (collision ? 2·base : base)`. In variable mode the
device signals a refresh collision by driving RWDS during CA; the controller samples RWDS across CA
and doubles accordingly. (Simplification of the full spec — fixed mode here means "no doubling",
documented so the controller and device agree.)

## Hardware handoff (superseded — kept as history)

This controller's own path to hardware (`quartus/hyperbus_smoke/` + `quartus/constraints/hyperbus.sdc`,
a synthesizable tristate-stub PHY) was never compiled and has been **removed** as dead weight:
PH3 closed the "real DDR-IO HyperBus PHY" gap by adopting the `third_party/hyperram` submodule's
silicon-proven PHY instead (`docs/ph3_submodule.md`), which now owns the hardware bring-up path
via `quartus/ph3_hyperram_char/`. This controller (`sim/replay/hbmc_core.sv`) remains simulation-only
test infrastructure and is not intended to reach hardware.
