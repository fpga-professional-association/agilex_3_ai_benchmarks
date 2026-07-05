# HyperBus / HyperRAM controller (issue #13)

Controller + behavioral device model for the Winbond **W957D8NB** (128 Mb = 16 MB, ×8 HyperBus DDR,
1.8 V) on the AXC3000. PLAN §4 flags this as the single biggest infrastructure gap: Quartus ships no
HyperBus controller and the stock Nios V example runs from internal RAM only.

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

**Decision:** implement a minimal clean-room Avalon-MM HyperBus controller (`rtl/hyperbus/hbmc_core.sv`),
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
`rtl/hyperbus/hbmc_core.sv` and `sim/hyperbus/w957d8nb_bfm.sv`.

Verified in `sim/hyperbus/tb_hyperbus.sv` (Verilator, `sim/hyperbus/run.sh`): device-register (ID)
read, single word R/W, linear burst R/W crossing a row boundary, and fixed vs variable latency with
and without a refresh collision.

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

## Hardware handoff (needs Quartus; no board until #14)

- `quartus/hyperbus_smoke/` + `quartus/constraints/hyperbus.sdc` are written but **not compiled here**
  (no Quartus in CI). Closing 100 MHz timing with the RWDS-referenced `.sdc` and recording the result
  is the remaining hardware/Quartus step, feeding PLAN's "closes at 166+ in GPIO?" risk item.
- The Agilex DDR-IO PHY (mapping `hb_dq_o/oe/i`, `hb_rwds_*`, `hb_capture_delay` to bidirectional IO
  with delay taps) is the Agilex-specific piece to add during bring-up; the smoke top uses plain
  behavioral tri-states as a placeholder so the controller can be fitted.

## Issue #14 addendum: capture trainer + memtest/bandwidth engines

L3 (PLAN §7) turns the HyperBus from "compiles at 100 MHz" into a measured operating point via three
new modules, all self-contained Avalon-MM masters onto `hbmc_core`'s existing `av_*`/`csr_*` slave
ports (see each file's own header for the exact integration contract):

- `rtl/hyperbus/hb_trainer.sv` — sweeps `hbmc_core`'s CAPDELAY tap, write/read-verifies a known
  pattern per tap, finds the widest contiguous passing run, and (if it meets `MIN_WINDOW`) parks
  CAPDELAY at the computed center.
- `rtl/microbench/l3_memtest/l3_memtest_engine.sv` — LFSR + address-in-data write pass, then N
  read-verify passes (`PASS_TARGET`), accumulating an `ERR_COUNT` and latching the first mismatch
  address.
- `rtl/microbench/l3_memtest/l3_bw_engine.sv` — streams `BURST_COUNT` back-to-back `BURST_WORDS`-word
  linear bursts (one direction per run) and cycle-counts the whole run for sustained-MB/s.

**hbmc_core's `av_burstcount` is only 8 bits** (issue #13) — a single HyperBus command tops out at
255 words (510 B). `l3_bw_engine` transparently decomposes any `BURST_WORDS` above that into
back-to-back sub-bursts; for the issue's 1 KB/4 KB sweep points this means several real HyperBus
CA+latency overheads per "burst" that a wider-bursting controller wouldn't pay. This is a genuine
controller limitation surfaced by this issue, not a modeling shortcut — expect the 1 KB/4 KB
efficiency numbers to look worse than 64 B/256 B for this reason alone, independent of anything the
real silicon does (PLAN §7 L3 step 5's "investigate controller dead cycles" applies here directly).

**Simulation honesty:** `sim/hyperbus/w957d8nb_bfm.sv` has no AC-timing/analog behavior at all (it is
a byte-per-beat protocol model, see above), so nothing about `hb_capture_delay` changes what a
simulated read returns. `sim/hyperbus/tb_hb_trainer.sv` adds a **testbench-only** synthetic
capture-delay error injector (flips a bit on BFM->controller read data outside a fixed "good" tap
range) purely so the window-search *algorithm* can be exercised deterministically under Verilator.
It is a test fixture, not a timing model, and the real pass/fail-vs-tap behavior can only be
observed on physical silicon — this issue's acceptance criteria (training window stability across
power cycles, zero-error memtest, the shmoo, sustained MB/s) are consequently all **hardware-gated**;
see the PR description's Hardware Handoff section.

**Host CSR access while these masters are idle.** None of the three modules above ever drives
`hbmc_core`'s CSR bus except `hb_trainer` (and only its own `CSR_CAPDELAY` writes during an active
sweep) — the host's own JTAG-Avalon/CSR master sets `hbmc_core`'s `LATENCY`/`CONFIG` once up front
and reads device registers directly, exactly as issue #13 already assumed. `hb_trainer`,
`l3_memtest_engine`, and `l3_bw_engine` are three separate masters that all ultimately need to reach
`hbmc_core`'s single Avalon/CSR slave port; the host only ever runs one of them at a time
(train -> memtest -> bandwidth sweep, PLAN §7 L3's own ordering), so a Platform Designer system wires
all of them plus the host's own master onto `hbmc_core` via the tool's generated multi-master
arbitration (standard Avalon-MM system-integration behavior) — not hand-rolled here, consistent with
`hbmc_core` itself staying PHY-agnostic (issue #13) and CDC wrappers only existing in `rtl/common/`
once an issue actually needs one. Each module's own testbench wires it 1:1 to a dedicated
`hbmc_core` instance, which is sufficient to validate its logic in isolation.

### CSR map (`hb_trainer`, own 32-bit register block, distinct from `hbmc_core`'s)

| Offset | Register | Function |
|---|---|---|
| 0x00 | CTRL | bit0 START (self-clearing) |
| 0x04 | STATUS | bit0 BUSY · bit1 DONE · bit2 WINDOW_VALID (width >= MIN_WINDOW) |
| 0x08 | WIN_LO | RO — lowest passing tap in the widest contiguous run |
| 0x0C | WIN_HI | RO — highest passing tap in the widest contiguous run |
| 0x10 | WIN_WIDTH | RO — WIN_HI - WIN_LO + 1 |
| 0x14 | WIN_CENTER | RO — also the value parked in `hbmc_core` CAPDELAY once DONE |
| 0x18 | NUM_TAPS | RO, = the `DELAY_TAPS` parameter |
| 0x1C | LAST_TAP | RO — current/last tap the sweep processed (progress readback) |

### CSR map (`l3_memtest_engine`)

| Offset | Register | Function |
|---|---|---|
| 0x00 | CTRL | bit0 START (self-clearing) |
| 0x04 | SEED | LFSR seed (0 is remapped to a fixed nonzero default) |
| 0x08 | BASE_ADDR | HyperRAM word base address under test |
| 0x0C | SPAN_WORDS | words covered per write / read-verify pass |
| 0x10 | PASS_TARGET | number of write+read-verify passes to run (>= 100 for the issue's acceptance) |
| 0x14 | STATUS | bit0 BUSY · bit1 DONE |
| 0x18 | PASS_DONE | RO — passes completed |
| 0x1C | ERR_COUNT | RO — cumulative mismatch count across all passes |
| 0x20 | ERR_ADDR | RO — word address of the first mismatch seen |

### CSR map (`l3_bw_engine`)

| Offset | Register | Function |
|---|---|---|
| 0x00 | CTRL | bit0 START (self-clearing) · bit1 DIR (1=read, 0=write) |
| 0x04 | BASE_ADDR | HyperRAM word base address for the run |
| 0x08 | BURST_WORDS | words per logical burst under test (the swept "N-byte burst" size) |
| 0x0C | BURST_COUNT | consecutive logical bursts to run back-to-back |
| 0x10 | STATUS | bit0 BUSY · bit1 DONE |
| 0x14 | CYCLES_LO | RO — elapsed cycles, low 32 (frozen once DONE) |
| 0x18 | CYCLES_HI | RO — elapsed cycles, high 32 |
| 0x1C | BURSTS_DONE | RO — logical bursts completed |

Host-side derivation (never computed in hardware, mirroring docs/register_map.md's own convention):

```
CYCLES = (CYCLES_HI << 32) | CYCLES_LO
sustained_mbps = (BURST_WORDS * 2 * BURST_COUNT) / (CYCLES / (f_HB * 1e6)) / 1e6
efficiency     = sustained_mbps / (2 * f_HB_mhz)     # PLAN §4; never quote 2xf_HB as "sustained"
error_rate     = ERR_COUNT / (SPAN_WORDS * PASS_DONE)
```

### Hardware handoff (issue #14)

Every acceptance criterion in issue #14 needs the physical AXC3000 board (RWDS/DQ skew, real
HyperRAM timing, real clock generation) — this doc's simulation coverage only proves the RTL's
control logic and CSR contract, exactly as the issue itself states ("this issue is meaningless in
simulation"). Nothing here should be read as a substitute for board bring-up. See the issue #14 PR
description for the precise list of what remains.
