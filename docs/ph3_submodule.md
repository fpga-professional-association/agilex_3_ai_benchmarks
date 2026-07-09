# PH3 submodule: `third_party/hyperram` adoption

This document is new this session. It explains what the `third_party/hyperram` git submodule is, why
PH3 adopted it in place of the earlier hand-rolled `hbmc_core` + tristate-stub PHY, exactly how it is
wired into `rtl/coredla_hyperram/axc3000_hyperram_axi4.sv`, and — most importantly — draws a hard line
between what this session **verified** and what remains **hardware handoff**. Read
`docs/ph3_status.md` first for the one-page summary; this doc is the detail behind it.

> **Post-session cleanup note (CoreDLA-HyperRAM rename):** the production glue described here moved
> from `rtl/hyperbus/` to `rtl/coredla_hyperram/`. The retired `hbmc_core`/`hyperbus_pkg` datapath
> moved to `sim/replay/` as test infrastructure and its package was renamed `hbmc_pkg` —
> **resolving the name-collision caveat below by construction** (the two packages simply have
> different names now; the careful separate-filelist discipline this section describes was the
> *workaround* while they shared a name). `sim/hyperbus/run_bridge.sh` and the standalone
> bridge-vs-`hbmc_core` TB it built have since been deleted as redundant (superseded end-to-end by
> `sim/hyperbus/run_hyperram_axi4.sh`), so references to it below are historical.

## What it is

`third_party/hyperram` is a git submodule pinned at commit **`c6f5d2b`** (branch `main`) of
[fpga-professional-association/hyperram](https://github.com/fpga-professional-association/hyperram)
— a clean-room, technology-agnostic HyperBus/HyperRAM controller IP with AXI4 and Avalon-MM
front-ends and a swappable DDR PHY. It is owned and maintained by another session/repo; **this repo
does not modify anything under `third_party/hyperram/`** (AGENTS.md "never invent" + the parent
task's explicit constraint). Everything below cites the submodule as-is at the pinned commit.

Why it replaced the earlier PH3 datapath: the prior `hbmc_core.sv` (now `sim/replay/hbmc_core.sv`,
test infrastructure — see the rename note above) was PHY-agnostic and
its wrapper (`axc3000_hyperram_axi4.sv`, pre-this-session) filled that PHY slot with a **thin SDR
tristate stub** (`hb_ck = clk`, `assign hb_dq = hb_dq_oe ? hb_dq_o : 'z`) — it synthesized and routed
but could not correctly clock a real W957D8NB. That was PH3 blocker #1
(`docs/ph3_status.md`/`docs/ph3_integration.md`, prior session). The submodule ships a **real,
datasheet-timed SDR PHY** (`hyperbus_phy_sdr`, `PHY_VARIANT="SDR"`) that has been run on silicon on
this exact board, closing that blocker.

## Measured bandwidth — the submodule's silicon measurement, not ours

The table below is quoted from `third_party/hyperram/README.md` ("Performance & test status"). It is
the **submodule's own** measured result, on the submodule's own `fpga/axc3000/` example design
(Arrow AXC3000, Agilex 3 `A3CY100BM16AE7S`, Winbond W957D8NB 128 Mb ×8 1.2 V, Quartus Prime Pro 26.1,
SDR PHY, data-integrity-verified every row, `ERR_COUNT=0`). **This PH3 branch has not yet measured
end-to-end bandwidth through its own `axi4_hbmc_bridge` → `hyperram_avalon` path on hardware** — the
number below characterizes the HyperBus IP in isolation, cited as context for the structural
bandwidth-ceiling discussion in `docs/ph3_interfaces.md` §d and PLAN §4/§5, not as a PH3 result.

| HyperBus CK | Byte clock | Write | Read |
|------------:|-----------:|------:|-----:|
| 50 MHz  | 100 MHz | 96.8  | 94.8 MB/s |
| 100 MHz | 200 MHz | 193.6 | 189.3 MB/s |
| 150 MHz | 300 MHz | 290.4 | 283.9 MB/s |
| 175 MHz | 350 MHz | 342.4 | 337.3 MB/s |

175 MHz CK is the submodule's own-stated SDR-PHY ceiling (the 2×-byte clock hits a min-pulse-width
limit there); the device's 200 MHz / 400 MB/s maximum needs a DDIO PHY variant, which the submodule
tracks as its own open issue #3 and does not yet ship. (Separately, the AXC3000 memory note recorded
in this repo's own memory — see below — already flags 200 MHz, not 250 MHz, as the W957D8NB's real
ceiling; the submodule's 175 MHz SDR figure is below that device ceiling, i.e. it is the *PHY's*
current limit, not the device's.) None of these clock points have been chosen, re-parameterized, or
re-measured by this PH3 branch; `LATENCY_CLOCKS=6`/`RD_PREAMBLE_SKIP` etc. in the wrapper are still
the submodule's sim defaults (see "Clock plan" below) pending a board bring-up pass.

The submodule also documents a device-level write-commit quirk (its issue #1: multi-burst writes can
drop the last word of each non-final burst; single-burst writes ≤512 words are the documented
workaround) and a burst-length ceiling (~768 words single-burst before a device refresh-window
collision, tCSM ≈15 µs). Neither has been exercised by this session's TB (which uses AWLEN ≤15, i.e.
≤256 words per AXI beat's worth of hbmc words, well under either limit) — flagged here so a future
session sizing larger bursts knows to re-read the submodule's `README.md`/`BW_TEST.md`.

## The wiring: bridge → `hyperram_avalon`

```
CoreDLA 256-bit AXI4  →  axi4_hbmc_bridge (unchanged, rtl/coredla_hyperram/axi4_hbmc_bridge.sv)
                      →  16-bit Avalon-MM (av_* / avs_*, 1:1 mapped)
                      →  hyperram_avalon (third_party/hyperram/rtl/hyperram_avalon.sv, pinned)
                      →  split HyperBus pins (hb_ck/hb_ck_n/hb_cs_n/hb_rst_n/hb_dq_*/hb_rwds_*)
```

`axi4_hbmc_bridge` (`docs/ph3_bridge_design.md`) is **byte-for-byte unchanged** by this swap — it
still speaks the same 16-bit-word Avalon master contract it always did.
`rtl/coredla_hyperram/axc3000_hyperram_axi4.sv` was rewritten to wire that Avalon master directly
onto `hyperram_avalon`'s Avalon slave instead of `hbmc_core`:

- `avs_address = { zero-pad, 1'b0, av_address }` — zero-extend the bridge's word address into
  `hyperram_avalon`'s wider `ADDR_WIDTH`, keeping the register-select MSB at 0 (memory space, not the
  CSR space).
- `avs_burstcount = { zero-pad, av_burstcount }` — same zero-extension for the wider `LEN_WIDTH`.
- `avs_byteenable = 2'b11` always — the bridge has no byte-enable output (it always drives full
  16-bit words; WSTRB partial-write detection is a separate sticky flag, not a byte-enable path —
  see `docs/ph3_bridge_design.md` "WSTRB / partial writes"), exactly as `third_party/hyperram/fpga/
  axc3000/top.sv` does it.
- `avs_read`/`avs_write`/`avs_writedata` pass straight through; `avs_readdata`/`avs_readdatavalid`/
  `avs_waitrequest` pass straight back.

`init_done` gating is automatic: `hyperram_avalon`'s front-end holds `avs_waitrequest` until the
controller is past POR/CR0 programming, and the bridge is already a proper Avalon master that honors
`waitrequest` — no extra gating logic was needed in the wrapper.

One correctness subtlety worth recording: `hyperram_avalon`'s own `INIT_CR0` default
(`HB_CR0_RESET`) is the device's raw POR image (5-clock latency), which is **not** derived from that
same module's `LATENCY_CLOCKS` default (6) — instantiating with `PROGRAM_CR=1` (the module default)
and leaving `INIT_CR0` at its default would program the *device* to 5-clock latency while the
*controller* FSM waits `LATENCY_CLOCKS` (6) cycles, a one-clock read-data misalignment. The wrapper
works around this by computing `HB_INIT_CR0` locally from `LATENCY_CLOCKS` (mirroring what
`hyperbus_ctrl.sv`'s own default does, and what the submodule's own `sim/tb_avalon.sv` does) — see
the `HB_INIT_CR0` localparam and its comment in `axc3000_hyperram_axi4.sv`.

## Clock plan: `clk` / `clk2x` / `clk_ref`

`hyperram_avalon` takes three clock inputs whose meaning depends on `PHY_VARIANT`
(`third_party/hyperram/rtl/hyperram_avalon.sv` header, `docs/INTEGRATION.md` §2 in the submodule):

| Port | GENERIC (sim) | SDR (board) |
|---|---|---|
| `clk` | word/CK-rate clock | word/CK-rate clock (= `hb_ck` rate) |
| `clk90` | genuine +90° phase, same rate as `clk` (centers the write DDR eye) | **repurposed as the 2× byte clock, 0° phase** — this is not a phase shift of `clk`, it is a faster clock |
| `clk_ref` | tie to `clk` | tie to `clk` |

The wrapper's port is named `clk2x` (not `clk90`) precisely to make this repurposing explicit at the
PH3 level — internally it is still wired to `hyperram_avalon.clk90`, but callers of the wrapper
should think of it as "the 2× byte clock the SDR PHY needs," not "a phase-shifted copy of clk." Under
Verilator (`PHY_VARIANT="GENERIC"`), the new TB drives `clk2x` as a genuine +90° copy of `clk`
(matching the submodule's own `sim/tb_avalon.sv` clocking, which the new TB's clock generation was
modeled on) — that satisfies the GENERIC PHY's actual use of the port. On a Quartus/SDR board build,
`clk2x` must instead be a **real second, faster clock** — the wrapper does not and cannot itself
regenerate that at simulation-clock rates; it is IOPLL work, tracked as the #1 item in
`docs/ph3_status.md` "What remains" and detailed in `docs/ph3_integration.md`'s clock-connections
section. `clk_ref` is tied to `clk` in the wrapper for both variants (`.clk_ref(clk)`), matching the
submodule's own guidance that both GENERIC and SDR ignore/tie this port.

`reset_n` (active-low, synchronous, from the PD `reset_handler`) is inverted once in the wrapper
(`rst = ~reset_n`) to match `hyperram_avalon`'s active-high synchronous `rst` convention.

## The `hyperbus_pkg` name-collision caveat (RESOLVED — kept as history)

`rtl/hyperbus/hyperbus_pkg.sv` (the pre-existing PH3 package, used by `hbmc_core.sv`) and
`third_party/hyperram/rtl/hyperbus_pkg.sv` (the submodule's package) used to both declare
`package hyperbus_pkg` — **same name, different contents**. SystemVerilog compilation units cannot
contain two packages with the same name, so any build that touched the new wrapper had to compile
**only** the submodule's copy and had to **never** also compile the in-repo `hyperbus_pkg.sv` or
`hbmc_core.sv` in the same run. `axi4_hbmc_bridge.sv` does not `import hyperbus_pkg::*`
(verified by inspection), so it was package-independent and composed cleanly with either package —
which is exactly why the bridge itself needed no changes. `sim/hyperbus/run_hyperram_axi4.sh` and
the now-removed `sim/hyperbus/run_bridge.sh` were two **separate** filelists/build directories for
this reason: the former compiles the submodule's package + `hyperram_avalon` (never the in-repo
package); the latter compiled the old package + `hbmc_core.sv` (never the submodule).

**Resolved by the CoreDLA-HyperRAM rename cleanup**: the in-repo package moved to
`sim/replay/hbmc_pkg.sv` and was renamed from `hyperbus_pkg` to `hbmc_pkg`, so it no longer shares a
name with `third_party/hyperram/rtl/hyperbus_pkg.sv` at all — the separate-filelist discipline above
is no longer load-bearing for new builds, just documented here for why `sim/replay/run.sh` (which
still compiles both `sim/replay/hbmc_pkg.sv` and, separately, the submodule's package is never in
the same build) keeps them apart.

## Verified this session vs. hardware handoff

**Verified this session (Verilator, `sim/hyperbus/run_hyperram_axi4.sh`, re-run to confirm PASS
while writing this doc — exit 0, `ALL AXC3000-HYPERRAM-AXI4 TBS PASSED`):**
- The wrapper (`axc3000_hyperram_axi4.sv`, `PHY_VARIANT="GENERIC"`) lints clean under
  `verilator --lint-only -Wall` with the submodule sources on the command line.
- Several AXI4 INCR write bursts (AWLEN 0..15, i.e. 1..16 beats of 256 bits = 16..256 HyperBus
  words) followed by read-back, byte-exact compare, against the submodule's own golden device model
  (`third_party/hyperram/sim/model/hyperram_model.sv`).
- `bresp`/`rresp` OKAY on every transaction; a WSTRB-partial trip-wire case exercises
  `wstrb_partial_seen`.
- No regression at the time: `sim/hyperbus/run_bridge.sh` (the old bridge-vs-`hbmc_core` TB, since
  removed as redundant coverage — see the rename note above) and `third_party/hyperram/sim/run.sh`
  (the submodule's own suite, untouched) both still passed.

**Structural (synthesized this session, against THIS submodule-backed wrapper):**
`quartus/ph3_hyperram_char` — `axc3000_hyperram_pads`→`axc3000_hyperram_axi4`→`hyperram_avalon`
(PHY_VARIANT="SDR", DIFF_CK=1) on A3CY100BM16AE7S. `quartus_syn`+`quartus_fit`+`quartus_sta` all
succeeded; **timing met** at CK=50 MHz / clk2x=100 MHz; 977 ALM / 0 DSP / 1 M20K / 1 PLL; Fmax
`clk` 237.4 MHz, `clk2x` 353.0 MHz restricted. HyperBus pins `false_path`'d, AXI slave `VIRTUAL_PIN`'d,
never programmed — fabric+I/O structural closure only, NOT board-timing closure. Full honesty box:
`quartus/ph3_hyperram_char/RESULTS.md`. The PD *component* `.tcl` was also updated to the two-clock
form and `ip-make-ipx` parse-checked. (The prior-session Qsys/`quartus_syn` *system* swap in
`docs/ph3_integration.md` predates the PHY swap and ran against the old stub-PHY wrapper.)

**Not done / hardware handoff (unchanged list from `docs/ph3_status.md`, restated here for this
doc's own completeness):**
1. PD clock-plan regeneration to actually deliver `clk2x` from an IOPLL on real hardware.
2. Board pinout + 25 MHz IOPLL reparam (`axc3000_board.tcl`, `user_ref_clk_freq_mhz 25`).
3. Regenerated `.sdc` for the HyperBus pins under the new clk/clk2x plan.
4. CoreDLA CSR start/done handshake (`sw/host/smoke_infer.py` `NotImplementedError`).
5. End-to-end HyperRAM bandwidth measurement through *this* PH3 path (bridge + `hyperram_avalon` +
   real CoreDLA traffic) — the bandwidth table above is the submodule's own isolated measurement,
   cited for context, not a PH3 result.

**No on-board inference has been run.** Nothing in this document claims hardware behavior for the
PH3 integration itself; only the cited submodule README measurement is a hardware number, and it is
explicitly the submodule's, not this branch's.

## Further reading (submodule docs, not modified, cited as-is)

- `third_party/hyperram/docs/INTEGRATION.md` — how to instantiate `hyperram_avalon`/`hyperram_axi`,
  the clk/clk90/clk_ref table this doc's "Clock plan" section is derived from, reset generation.
- `third_party/hyperram/docs/PHY_PORTING.md` — the frozen `hyperbus_phy` port contract, PHY_VARIANT
  selection mechanism, and the GENERIC/INTEL/XILINX skeleton status (SDR is the board-proven variant
  used here; INTEL/XILINX are unrelated vendor-primitive skeletons this repo does not use).
- `third_party/hyperram/docs/INTERFACES.md` — the frozen module-boundary port lists this document's
  wiring section is quoting from.
- `third_party/hyperram/README.md` — the measured-bandwidth table, known device quirks (issue #1
  write-commit, issue #3 DDIO-PHY-for-200MHz), and burst-length limits cited above.
