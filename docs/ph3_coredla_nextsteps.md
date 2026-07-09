# PH3 → CoreDLA-on-AXC3000: the concrete next-steps roadmap

This is the build plan for the five items `docs/ph3_status.md` "What remains" lists, now that:

1. the memory-subsystem gap is **structurally closed** (`rtl/hyperbus/axc3000_hyperram_axi4.sv` +
   `axi4_hbmc_bridge`, sim-verified, `docs/ph3_bridge_design.md`/`docs/ph3_interfaces.md`),
2. a real, **silicon-proven** HyperBus SDR PHY exists (`third_party/hyperram`, this session's own
   measurement on this exact board: **342.4 MB/s write / 337.3 MB/s read at 175 MHz CK**,
   `third_party/hyperram/fpga/axc3000/README.md` "Measured on hardware"), and
3. the board bring-up loop (attach → program → JTAG readback) is **proven working this session**
   (`docs/toolchain.md`, the `hyperram` submodule's own `bw.sof` program/measure cycle).

Each item below states **what to do** (file paths + commands, mirroring the now-proven
`third_party/hyperram/fpga/axc3000/` flow wherever it applies), **whether it's desk-doable now,
needs the board, or is blocked** (and on what), and **the concrete artifact that proves it done**.
Nothing here is measured yet unless explicitly labeled so; anything computed from an assumption is
marked **ESTIMATE**.

Read first (unchanged by this doc): `docs/ph3_status.md`, `docs/ph3_integration.md`,
`docs/ph3_interfaces.md`, `docs/ph3_submodule.md`, `docs/ph3_bridge_design.md`, `AGENTS.md`.

---

## Item 1 — Full PD *system* regeneration to source `clk2x` from an IOPLL

**What it closes:** `docs/ph3_status.md` #1. The PD *component* (`quartus/ip/axc3000_hyperram_axi4/
axc3000_hyperram_axi4_hw.tcl`) already declares the two-clock (`clk` + `clk2x`) interface and
parse-checks clean; the standalone `quartus/ph3_hyperram_char` build already proves the wrapper fits
and times with both clocks driven by a *local* IOPLL
(`quartus/ph3_hyperram_char/qsys/make_char_clkgen.tcl`, CK=50 MHz/clk2x=100 MHz, timing met,
`RESULTS.md`). What's missing is wiring that same local-IOPLL pattern into the actual CoreDLA
*system* (`ed_zero.tcl`), replacing the single-clock `jtag_pll.outclk0 → hyperram_0.clk` connection
`docs/ph3_integration.md` §"Clock connections" narrates from the *pre-submodule* stub-PHY session.

**Exact recipe** (mirrors `make_char_clkgen.tcl`, which itself mirrors
`third_party/hyperram/fpga/axc3000/qsys/make_bw_sys.tcl`'s proven IOPLL-outclk0/outclk1 idiom):

1. In `_ph3_ed_hyperram/hw/qsys/ed_zero.tcl` (the working copy from the prior session's swap
   attempt — regenerate per `docs/ph3_interfaces.md` "Provenance" if stale), add a second IOPLL
   output clock to the existing `jtag_pll` `altera_iopll` instance (or add a second, dedicated
   IOPLL instance — `make_char_clkgen.tcl`'s pattern uses one IOPLL with two outputs, which is
   simpler and is the pattern to copy):
   ```tcl
   set_instance_parameter_value jtag_pll gui_number_of_clocks        {2}
   set_instance_parameter_value jtag_pll gui_output_clock_frequency1 [expr {2.0 * $CK_MHZ}]
   set_instance_parameter_value jtag_pll gui_phase_shift_deg1        {0.0}
   ```
   (both outputs at 0°, per `make_char_clkgen.tcl`'s comment — the SDR PHY derives its
   CK-centring shift from `clk2x`'s own negedge, not from a PLL phase; this is the fix that avoided
   Fitter errs 24403/24404 in the submodule's own history, see `third_party/hyperram/fpga/axc3000/
   README.md` "History").
2. Add one `add_connection jtag_pll.outclk1 hyperram_0.clk2x` alongside the existing
   `jtag_pll.outclk0 → hyperram_0.clk` connection (`docs/ph3_integration.md` line ~141).
3. Re-run the same qsys-generate + `quartus_syn` bounded-attempt sequence
   `docs/ph3_integration.md` "Reproduce" already documents, now against `_ph3_ed_hyperram` (which
   must first have the submodule-backed `.ip`/component regenerated — see item 2's "Regenerating"
   block in `quartus/ph3_hyperram_char/RESULTS.md` for the two-command pattern:
   `qsys-script … --quartus-project=… && qsys-generate … --quartus-project=…`, both required or the
   IOPLL/hyperram_0 come back as black boxes).
4. `qsys-generate -syn --part=A3CY100BM16AE7S shell.qsys` then `quartus_syn top` — same commands as
   `docs/ph3_integration.md` "Reproduce", just against the two-clock component.

**Desk-doable now.** Nothing here needs the physical board — `make_char_clkgen.tcl` already proves
this exact IOPLL two-output pattern generates and synthesizes on `A3CY100BM16AE7S` in the Quartus
Pro 26.1 Docker toolchain, and `ed_zero.tcl` is Tcl text editable without hardware.

**Proof artifact:** `qsys-generate -syn` reporting "Finished: Platform Designer system generation, 0
errors" on `shell.qsys` with **two** clock connections into `hyperram_0` (visible in the generated
`shell.qsys` XML or a `qsys-generate --print-connections`-style dump), followed by `quartus_syn top`
succeeding with 0 errors (same bar `docs/ph3_integration.md`'s prior attempt cleared for the
one-clock system). Record the exact command + log tail in an updated
`docs/ph3_integration.md`-style note or a new `quartus/ph3_ed_hyperram/RESULTS.md`.

---

## Item 2 — Board pinout + 25 MHz IOPLL reparam

**What it closes:** `docs/ph3_status.md` #2.

**What to do:**
1. Pinout: `quartus/constraints/axc3000_board.tcl` **already exists and is populated** (clock,
   reset, LEDs, HyperRAM section) — it was written for the `third_party/hyperram` bring-up and the
   PH3 `ph3_hyperram_char` build already sources it successfully (25 MHz `CLK_25M_C`@A7,
   `hb_cs_n=D8`/`hb_ck=D7` per the submodule's own hardware-corrected pinout,
   `third_party/hyperram/fpga/axc3000/README.md` "Hardware handoff / notes"). The remaining work is
   `source`-ing it from `_ph3_ed_hyperram/hw/top.qsf` (replace the stock C-series-E6 device pin
   assignments with `source ../../../quartus/constraints/axc3000_board.tcl`, per
   `docs/ph3_integration.md` "top.sv port swap" § "top.qsf" bullet) and matching top.sv's HyperBus
   port *names* to what the file assigns to (`hb_dq`, `hb_rwds`, `hb_cs_n`, `hb_ck`, `hb_rst_n` —
   already the names `docs/ph3_integration.md`'s top.sv swap recipe uses).
2. IOPLL reparam: in `ed_zero.tcl`, set `user_ref_clk_freq_mhz 25` (line ~1087 per
   `docs/ph3_integration.md`) so both `dla_pll` and `jtag_pll` regenerate their M/N/C dividers for a
   25 MHz reference instead of the stock example's assumed 100 MHz. This is an ordinary IOPLL
   re-parameterization (changes only divider values, no netlist topology) — the design's own
   `dla_adjust_pll.tcl` re-tunes `clk_dla`'s achievable Fmax afterward.
3. Also set `DEVICE {A3CY100BM16AE7S}` (already applied per `docs/ph3_integration.md` item 0) and
   drop the stock `BOARD` C-series qualifier from `top.qsf`.

**Desk-doable now.** `axc3000_board.tcl` is committed and cited by two independent successful builds
already (`ph3_hyperram_char`, and the submodule's own `bw.sof`, which **ran on real silicon this
session** per project memory). Sourcing it into `_ph3_ed_hyperram`'s `.qsf` and flipping one Tcl
variable needs no board access.

**Proof artifact:** `quartus_fit` on the full system entering place-and-route with the real board
pin locations bound (not `VIRTUAL_PIN`'d) and 0 unresolved pin-assignment errors; `dla_adjust_pll.tcl`
printing the retuned `clk_dla` Fmax for the 25 MHz reference case.

---

## Item 3 — Regenerated `.sdc`

**What it closes:** `docs/ph3_status.md` #3. `top.out.sdc` (generated by the stock flow) still
constrains the old `emif`-derived clocks; it must instead constrain `jtag_pll.outclk0`/`outclk1`
(now `clk`/`clk2x`) + `dla_pll.outclk0`, with the HyperBus pins false-pathed exactly as
`quartus/ph3_hyperram_char.sdc` already does for the standalone char build:

```sdc
create_clock -name CLK_25M_C -period 40.000 [get_ports {CLK_25M_C}]
derive_pll_clocks
derive_clock_uncertainty
set_false_path -to   [get_ports {hb_dq[*] hb_rwds hb_cs_n hb_ck hb_rst_n}]
set_false_path -from [get_ports {hb_dq[*] hb_rwds}]
```

**What to do:** either (a) let the vendor flow regenerate `top.out.sdc` fresh once items 1-2 land
(the standard AI-Suite `generate_sof.tcl` path does this automatically off the qsys system), or (b)
hand-adapt `quartus/constraints/ph3_hyperram_char.sdc` (already proven: `derive_pll_clocks` finds and
constrains the two `char_clkgen` IOPLL outputs with 0 SDC errors in that build) into a new
`quartus/constraints/ph3_ed_hyperram.sdc`, keeping the false-paths on the HyperBus pins (bring-up
style — board-timing closure to the W957D8NB's tDSS/tDSH/tCKD is explicitly **not** attempted here,
same honesty-box scope as `ph3_hyperram_char.sdc`) and adding `dla_pll`'s clock alongside.

**Desk-doable now** — same reasoning as items 1-2, this is Tcl/SDC text work against a proven
template, no board needed. Only the *quality* of the false-pathed HyperBus timing (real board-timing
closure) needs the board, and that is explicitly out of scope for this item (it's PH3_SUBMODULE_SPEC
follow-on work, not blocking a first functional bring-up at a conservative clock).

**Proof artifact:** `quartus_sta` on the full system reporting `derive_pll_clocks` found both memory
clocks and the DLA clock, TNS=0 or a small positive/negative slack number that is *reported*
(not silently absent because a clock was never constrained) — i.e. the same class of clean STA
report `ph3_hyperram_char.sta.rpt` already produced for the standalone wrapper.

---

## Item 4 — CoreDLA CSR start/done handshake

**What it closes:** `docs/ph3_status.md` #4; `sw/host/smoke_infer.py`'s
`SystemConsoleTransport.run_inference()` `NotImplementedError`.

**What to do:** the CSR bit(s) that start one inference and signal completion are internal to the
vendor's OpenVINO FPGA plugin (`libcoreDlaRuntimePlugin.so`) and are not in any plaintext doc this
repo has read yet. Two concrete, desk-doable-now avenues before anything needs the board:
1. **Search the vendor runtime tree already present in the toolchain Docker image** for the actual
   CSR poke sequence — `sw/host/smoke_infer.py`'s own docstring already names the exact files to
   grep: `/opt/altera/fpga_ai_suite/ubuntu/dla/runtime/coredla_device/mmd/system_console/
   system_console_script.tcl` and `mmd_wrapper.cpp`, inside the `alterafpga/fpgaaisuite:2026.1.1-
   quartus` image (`docker run --rm -i alterafpga/fpgaaisuite:2026.1.1-quartus bash -lc "grep -n
   ... /opt/altera/.../mmd_wrapper.cpp"`). If the plugin issues its start/done CSR writes/polls
   through this MMD, the sequence is likely in there in some form (register offsets, poll loop),
   even if not in a human-readable spec.
2. **Capture a live CSR trace with the vendor's own `system_console` tooling** once the board is
   programmed with a *stock* (unmodified) example design that already runs inference successfully
   on a supported devkit (if the FPGA AI Suite ships one for a devkit this repo has access to) —
   watching what the vendor runtime itself writes to `g_const_master_offset_dla` (0x8000_0000 range,
   already known from `smoke_infer.py`'s own docstring) via JTAG/System Console would reverse-engineer
   the handshake without needing SignalTap.
3. Failing both, a **SignalTap capture on real hardware** of the CSR bus during a vendor-run
   inference is the fallback path explicitly named in `smoke_infer.py`'s docstring.

**Desk-doable now:** avenue (1), grepping the already-present Docker image — genuinely free, no
board. **Needs the board:** avenues (2)/(3). **Blocked on:** no public vendor CSR-map document has
surfaced in anything this repo has read; this is the single most vendor-opaque item on the list.

**Proof artifact:** either a documented CSR offset/bit-sequence (with citation to the exact vendor
file/line it came from) that `smoke_infer.py`'s `SystemConsoleTransport.run_inference()` can
implement without `NotImplementedError`, or (fallback) a SignalTap/System-Console trace file showing
the observed start/done pokes with a written interpretation.

---

## Item 5 — HyperRAM bandwidth ceiling (end-to-end, this PH3 path)

**What it closes:** `docs/ph3_status.md` #5. This is explicitly **not a bug**: CoreDLA's 256-bit
AXI4 DDR port is ~16× width-starved vs. the 16-bit HyperBus word
(`docs/ph3_interfaces.md` §d), so inference in this system will be HyperRAM-bandwidth-bound. What's
missing is measuring *this PH3 path's* (bridge + `hyperram_avalon` + real CoreDLA traffic)
sustained bandwidth end-to-end, rather than citing the submodule's own isolated measurement.

**What to do, staged:**
1. **Desk-doable now:** extend `sim/hyperbus/tb_axc3000_hyperram_axi4.sv` (or a sibling TB) with a
   longer, realistic burst mix (multiple back-to-back AXI4 INCR bursts at AWLEN=15, matching
   CoreDLA's actual max burst) and count simulated cycles to derive a **simulated** sustained
   MB/s bound through the bridge — still `kind: "estimate"` (it's a clock-cycle count in a
   testbench, not a hardware measurement), but a tighter estimate than the submodule's isolated
   number because it includes the bridge's per-beat 16-word-hbmc-burst overhead
   (`docs/ph3_bridge_design.md` "Throughput note" — ~40-75% overhead per 16-word beat, uncoalesced).
2. **Needs the board:** once items 1-4 land and a `.sof` programs, drive real CoreDLA DMA traffic (or,
   short of a working CSR handshake, a synthetic traffic generator standing in for CoreDLA's AXI4
   master — mirroring how the submodule's own `rtl/bench/hyperram_bw_test.sv` measured the submodule
   in isolation) through this bridge and count `WR_CYCLES`/`RD_CYCLES` on-chip, read back over JTAG
   (control-plane only, PLAN §8 method E — never use JTAG as the timed data path), exactly the
   pattern `third_party/hyperram/fpga/axc3000/sysconsole/bw_read.tcl` already proves end-to-end on
   this exact board.
3. Record the result as `results/ph3_hyperram-axi4-bridge-sustained-bw_<date>.json`,
   `kind: "measured"`, `level: "PH3"`, once (2) actually runs; keep the item-1 simulated number
   labeled `kind: "estimate"` in the meantime — never overwrite a measurement with an estimate
   (AGENTS.md).

**Proof artifact:** a schema-valid `results/*.json` with `metrics.sustained_mbps` (and,
once possible, `metrics.fps` for a real compiled model) plus `config.hyperbus_mhz` — cited, not
invented, per `results/schema/result.schema.json`.

---

## What we can measure the day it runs

The estimator (`scripts/estimate.py`, issue #6/PH0) already shows `ad-toycar`'s FPS is **linear in
assumed external-memory bandwidth** — `results/reports/ph0_estimator.md` "FPS scales roughly linearly
with assumed BW in every case (`memory_bound: true` in every one of these 12 JSONs)" — because at
these bandwidth points `ad-toycar` never reaches its compute ceiling on `AGX3_Performance.arch`; its
FPS is a direct function of `--fassumed-memory-bandwidth`.

The existing sweep already measured (as `kind: "estimate"`, not hardware) the FPS at four bandwidth
points:

| assumed BW (MB/s) | ad-toycar FPS | source |
|---:|---:|---|
| 200 | 417.21 | `results/ph0_ad-toycar-agx3-performance-estimator-200mbps_20260704.json` |
| 250 | 521.58 | `results/ph0_ad-toycar-agx3-performance-estimator-250mbps_20260704.json` (also `_20260707.json`) |
| 333 | 730.22 | `results/ph0_ad-toycar-agx3-performance-estimator-333mbps_20260704.json` |
| 400 | 884.28 | `results/ph0_ad-toycar-agx3-performance-estimator-400mbps_20260704.json` |

**ESTIMATE — recomputed at 342 MB/s, the now-measured HyperRAM write ceiling** (this session's own
`hyperram` submodule silicon measurement, 175 MHz CK, `third_party/hyperram/README.md`/
`docs/ph3_submodule.md`, **not** the 250 MB/s figure the original PLAN planning point assumed).

A fresh `scripts/estimate.py --model ad-toycar --arch models/arch/AGX3_Performance.arch --membw 342`
run (the same `dla_compiler --fanalyze-performance` invocation the existing sweep used, just at the
new bandwidth point) was started during this session's work but had not finished compiling by the
time this document was written (`dla_compiler` runs several minutes per invocation through the
Docker-backed toolchain, same as the existing four sweep points did). Rather than block this
document on it, the arithmetic below **interpolates between the two closest already-published,
schema-valid estimator JSONs that bracket 342 MB/s** (333 and 400 MB/s — both real `dla_compiler`
runs, not invented), which is a tighter estimate than a naive single-point scaling because the
FPS-vs-BW relationship is only "roughly" (not exactly) linear across the full sweep
(`results/reports/ph0_estimator.md`):

```
333 MB/s -> 730.22 fps   (results/ph0_ad-toycar-agx3-performance-estimator-333mbps_20260704.json)
400 MB/s -> 884.28 fps   (results/ph0_ad-toycar-agx3-performance-estimator-400mbps_20260704.json)

local slope = (884.28 - 730.22) fps / (400 - 333) MB/s = 154.06 / 67 = 2.2994 fps per MB/s

fps(342 MB/s) ≈ 730.22 + (342 - 333) × 2.2994
             ≈ 730.22 + 9 × 2.2994
             ≈ 730.22 + 20.69
             ≈ 750.9 fps        <-- ESTIMATE, interpolated, not a dla_compiler output
```

For comparison, the cruder proportional scaling straight off the 250 MB/s point that PLAN's own
"default planning point" uses (`fps ≈ fps_250 × bw/250`, valid only if the relationship were exactly
linear, which `results/reports/ph0_estimator.md` explicitly says it is only "roughly"):

```
521.58 × 342/250 ≈ 713.4 fps        <-- cruder ESTIMATE, shown only for context
```

The two hand-arithmetic estimates (750.9 vs. 713.4, a ~5% spread) bracket where a real
`--membw 342` `dla_compiler` run would likely land; **if/when that direct run completes it
supersedes both of these interpolations** and should be written as its own
`results/ph0_ad-toycar-agx3-performance-estimator-342mbps_<date>.json` via `scripts/estimate.py`
exactly like the existing four points, not hand-copied into this document.

Once the board runs (items 1-4 above land and item 5 measures real sustained bandwidth), what's
directly measurable via the existing `results/` schema:
- **`metrics.fps`** — end-to-end inferences/sec, from `sw/host/smoke_infer.py` once its CSR
  handshake (item 4) is real, `kind: "measured"`.
- **`metrics.latency_us_p50`/`p99`/`min`/`max`** — from repeated `run_inference()` calls timed on the
  host side of the JTAG control plane (control-plane timing overhead must be subtracted or at least
  disclosed — PLAN §8 method E already flags JTAG as control-plane-only, never the timed data path).
- **`metrics.sustained_mbps`** — item 5's on-chip cycle-counter readback (mirroring
  `hyperram_bw_test`'s `WR_CYCLES`/`RD_CYCLES` pattern), `kind: "measured"`.
- **`config.utilization.alm/dsp/dsp_tensor_mode/m20k`** — from the Fitter report of the actual
  compiled system (`quartus_fit`'s `.fit.summary`, cross-checked with `scripts/audit_tensor_mode.py`
  per AGENTS.md's Quartus discipline — CoreDLA's own DSPs must be audited for tensor-mode fallback
  the same as any custom RTL).
- **`config.fclk_mhz`/`hyperbus_mhz`** — the achieved `clk_dla`/`clk`/`clk2x` operating points from
  STA (item 3), mandatory fields for any `kind: "measured"` on-fabric result per the schema.

None of the numbers in this section are hardware measurements — the four 200/250/333/400 MB/s rows
are `kind: "estimate"` estimator JSONs already in `results/`, and the 342 MB/s figure above is a
hand interpolation between two of them (explicitly not yet a `dla_compiler` output). Everything else
is future work gated on items 1-5.
