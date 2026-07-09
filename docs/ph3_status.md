# PH3 status — on-board AI enablement (HyperRAM ↔ CoreDLA)

Entry point for the PH3 effort: replace CoreDLA's LPDDR4 EMIF (which the AXC3000 lacks) with a
HyperRAM-backed AXI4 memory so an FPGA-AI-Suite model can eventually run on the board. Branch
`ph3-hyperram-axi4-coredla`.

> **Post-session cleanup note (CoreDLA-HyperRAM rename):** the production glue below
> (`axi4_hbmc_bridge.sv`, `axc3000_hyperram_axi4.sv`, `axc3000_hyperram_pads.sv`) moved from
> `rtl/hyperbus/` to `rtl/coredla_hyperram/`. The old `hbmc_core`/`hyperbus_pkg` datapath (retired by
> the submodule adoption below) was relocated to `sim/replay/` as test infrastructure and its
> package renamed `hbmc_pkg` — resolving the name-collision caveat this doc describes below by
> construction. Its now-redundant standalone TB/build (`sim/hyperbus/tb_hyperbus.sv`,
> `tb_axi4_hbmc_bridge.sv`, `run.sh`, `run_bridge.sh`) and the superseded
> `quartus/hyperbus_smoke/`/`quartus/ph3_bridge_char/` Quartus projects were deleted. Paths below are
> updated to match; bullets that cite a removed command/file are annotated inline.

## Documents (read in order)
1. [`ph3_interfaces.md`](ph3_interfaces.md) — reverse-engineered CoreDLA AXI4 "DDR" master (256-bit,
   const 32 B/beat INCR, reduced AXI4, `arid→rid`) + the adaptation gap + the bandwidth reality.
2. [`ph3_bridge_design.md`](ph3_bridge_design.md) — the `axi4_hbmc_bridge` FSM, address/width
   mapping, WSTRB scope, v1 limitations. The bridge itself is unchanged by the submodule swap below.
3. [`ph3_integration.md`](ph3_integration.md) — the Platform Designer component + the exact
   `ed_zero.tcl` / `top.sv` swap recipe + the bounded compile attempt + what remains.
4. [`ph3_submodule.md`](ph3_submodule.md) — **new this session**: the `third_party/hyperram`
   submodule (pinned commit `c6f5d2b`), why it replaced the old `hbmc_core` + tristate-stub PHY
   datapath, the clk/clk2x/clk_ref wiring, the `hyperbus_pkg` name-collision caveat, and the
   verified-this-session vs hardware-handoff boundary.

## Done this session
- **Adopted the `third_party/hyperram` submodule** (pinned commit `c6f5d2b`) as the HyperBus
  controller + PHY, replacing `hbmc_core.sv` (now `sim/replay/hbmc_core.sv`, test infrastructure —
  see the rename note above) and the old tristate-stub PHY. This closes
  PH3 blocker #1 below: the submodule's SDR PHY is a **real, silicon-proven DDR-IO PHY**, not a
  stub — see `docs/ph3_submodule.md` for the measured-bandwidth table (the submodule's own
  silicon measurement, cited not re-measured here).
- **`rtl/coredla_hyperram/axc3000_hyperram_axi4.sv`** — rewritten: `axi4_hbmc_bridge` (unchanged) →
  submodule `hyperram_avalon` (ctrl + PHY), **split** HyperBus pins (`hb_dq_o/oe/i`,
  `hb_rwds_o/oe/i`, no `inout`), `clk`/`clk2x`/`reset_n` clocking (see `ph3_integration.md`).
  **sim-verified** (Verilator PASS, see below); CoreDLA-facing AXI4 slave port list unchanged.
- **`rtl/coredla_hyperram/axc3000_hyperram_pads.sv`** — new tiny board-pads wrapper that turns the
  split `hb_dq`/`hb_rwds` pins into real `inout` balls for synthesis/board use; pure wiring, not
  Verilator-exercised itself (the split-pin wrapper is what the TB drives).
- **`sim/hyperbus/tb_axc3000_hyperram_axi4.sv`** + **`sim/hyperbus/run_hyperram_axi4.sh`** — new
  self-checking TB against the submodule's golden `hyperram_model.sv` (PHY_VARIANT="GENERIC"):
  multiple AXI4 INCR write bursts (AWLEN 0..15) + byte-exact read-back + a WSTRB-partial trip-wire
  check. **`bash sim/hyperbus/run_hyperram_axi4.sh` → PASS** (`ALL AXC3000-HYPERRAM-AXI4 TBS
  PASSED`, re-run this session, exit 0). The submodule's own sim still passes unmodified
  (`third_party/hyperram/sim/run.sh`) — no regression. The old bridge-vs-`hbmc_core` standalone TB
  (`sim/hyperbus/tb_axi4_hbmc_bridge.sv`/`run_bridge.sh`) that also passed unmodified at the time has
  since been **removed** as redundant coverage (CoreDLA-HyperRAM rename cleanup) — this TB is now
  the sole bridge regression.
- **`rtl/coredla_hyperram/axi4_hbmc_bridge.sv`** — unchanged from the prior session. Originally
  **sim-verified** against the real `hbmc_core` + W957D8NB BFM via a standalone TB
  (`tb_axi4_hbmc_bridge.sv`/`run_bridge.sh`, since removed as redundant) and, through the new
  wrapper, against the submodule (still verified, see above).
- **Bridge fmax/P&R on A3CY100** — 273.6 MHz, 1,014 ALM / 0 M20K / 0 DSP, prior session, in the
  now-removed `quartus/ph3_bridge_char` project (superseded by `quartus/ph3_hyperram_char`, see
  below). **synthesized**, historical result — the bridge RTL has not changed since, and
  `quartus/ph3_hyperram_char` now covers the full submodule-backed wrapper's fmax/P&R instead.
- **PD component updated** — `quartus/ip/axc3000_hyperram_axi4/axc3000_hyperram_axi4_hw.tcl`
  regenerated for the split-pin/pads wrapper: filesets now list the submodule sources (not
  `hbmc_core.sv`), a second `clk2x` clock sink and an `init_done` status port were added, `hb_ck_n`
  added to the HyperBus conduit, and `TOP_LEVEL` set to `axc3000_hyperram_pads` (the inout-ball
  wrapper). **Parse-checked** clean this session via `ip-make-ipx` on the Quartus Pro 26.1 Docker
  toolchain ("Found 1 components", exit 0). Full SV port-width binding is only validated at
  qsys-generate/system-integration time (item 1 below).
- **`quartus/ph3_hyperram_char` — standalone structural build against the new submodule-backed
  wrapper** (PHY_VARIANT="SDR", DIFF_CK=1; `axc3000_hyperram_pads`→wrapper→`hyperram_avalon`).
  `quartus_syn`+`quartus_fit`+`quartus_sta` all ran to success on A3CY100BM16AE7S (~2 min).
  **Measured (structural):** 977 ALM (3%) / 0 DSP / 1 M20K / 1 PLL; **timing met** at the
  conservative CK=50 MHz / clk2x=100 MHz operating point (+3.82 ns / +8.71 ns setup slack). Fmax:
  `clk` 237.4 MHz, `clk2x` 353.0 MHz restricted (min-pulse-width limited) — matching the submodule's
  own ~353 MHz byte-clock reference. HyperBus pins are `false_path`'d (bring-up style), AXI slave
  `VIRTUAL_PIN`'d (no traffic gen), never programmed — see `quartus/ph3_hyperram_char/RESULTS.md`
  for the full honesty box. This is fabric+I/O structural closure, NOT board-timing closure.
- **The prior EMIF→HyperRAM swap** (prior session) — generated in Qsys and `quartus_syn` clean on
  A3CY100 against the *old* stub-PHY wrapper. **synthesized (structural)**, not a signed-off
  bitstream. Re-running that full `ed_zero.tcl` system swap against the new submodule-backed
  component (wiring `clk2x` from the IOPLL) is item 1 below.

## What remains for a model to classify on hardware (priority order)
PH3 blocker #1 ("a real DDR-IO HyperBus PHY") from the prior session is **CLOSED** — the submodule's
SDR PHY is real silicon-measured hardware, not a stub. What remains is now board/system integration,
not controller RTL:
1. **Full PD *system* regeneration to source `clk2x` from the IOPLL.** The PD *component*
   (`…/axc3000_hyperram_axi4_hw.tcl`) is already the two-clock version (`clk` + `clk2x`, parse-checked
   above), and the standalone `quartus/ph3_hyperram_char` build proves the wrapper fits and times with
   both clocks driven by a local IOPLL. What remains is re-running the CoreDLA example *system* swap
   (`ed_zero.tcl`) against this new component so the IOPLL there actually generates and wires `clk2x`
   (the prior swap, on the old stub, needed only one clock). See `docs/ph3_integration.md` clock
   section.
2. **Board pinout + 25 MHz IOPLL reparam** (unchanged from prior session — `axc3000_board.tcl`,
   `user_ref_clk_freq_mhz 25`) → meaningful signed-off fit/STA.
3. **Regenerated `.sdc`** for the HyperBus pins under the new clk/clk2x plan.
4. **CoreDLA CSR start/done handshake** (undocumented; `sw/host/smoke_infer.py`
   `NotImplementedError`) — unchanged from prior session.
5. **HyperRAM bandwidth ceiling** — still structural, not a bug: CoreDLA's 256-bit AXI4 port is
   ~16× width-starved vs the 16-bit HyperBus word (`docs/ph3_interfaces.md` §d, PLAN §4/§5). The
   submodule's own silicon measurement (up to ~342 MB/s write / ~337 MB/s read at 175 MHz CK,
   `docs/ph3_submodule.md`) is the best available number for *this* HyperBus IP on *this* board, but
   it is not yet measured through the full CoreDLA→bridge→hyperram_avalon path end-to-end.

## Reproduce
- New wrapper sim (submodule-backed, sole bridge regression): `bash sim/hyperbus/run_hyperram_axi4.sh`
- Record-replay integration sim (exercises the retired `hbmc_core`/`hbmc_pkg` test infrastructure,
  now at `sim/replay/`): `bash sim/replay/run.sh`
- Submodule sanity (untouched): `bash third_party/hyperram/sim/run.sh`
- New submodule-backed wrapper structural build (SDR PHY, supersedes the removed `ph3_bridge_char`):
  `bash scripts/build.sh ph3_hyperram_char` (or the qsys-regen sequence in
  `quartus/ph3_hyperram_char/RESULTS.md` → "Regenerating").
- PD component parse-check: `source scripts/env.sh && ip-make-ipx --source-directory=quartus/ip/axc3000_hyperram_axi4`
- The swap attempt (prior session, old stub-PHY wrapper): see `ph3_integration.md` → "Reproduce".
- The old bridge-vs-`hbmc_core` standalone sim (`sim/hyperbus/run_bridge.sh`) and the old
  `quartus/ph3_bridge_char` fmax/P&R build have been **removed** as redundant (CoreDLA-HyperRAM
  rename cleanup) — their historical results are cited above, not reproducible commands anymore.

## Bottom line
The memory-subsystem gap that prior work (`issue-7-hostless-jtag`, `docs/board_bringup.md` §2f)
flagged as *the* blocker to any AXC3000 CoreDLA build was already **structurally closed** (generates
+ synthesizes clean) in the prior session, and this session closes the PHY gap on top of that: the
datapath now terminates in a **real, silicon-proven HyperBus PHY** (the `third_party/hyperram`
submodule, measured on this exact board) rather than a tristate stub. A *functional* on-board
CoreDLA inference is still gated on the five items above — principally the PD clock-plan
regeneration to actually deliver `clk2x` and a board fit against the new wrapper. Surrounding
context: model-compile coverage in `results/reports/ph0_estimator.md` (branch
`issue-6-ph0-estimator`); the original memory-subsystem analysis in `docs/board_bringup.md` §2f
(branch `issue-7-hostless-jtag`).
