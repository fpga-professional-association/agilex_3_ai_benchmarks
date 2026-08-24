# FPGA AI Suite speed hunt — AXC3000 / CoreDLA / ResNet-8

**Date:** 2026-08-21 · **Board:** Arrow AXC3000 (Agilex 3 `A3CY100BM16AE7S`, speed grade 7)
**Toolchain:** FPGA AI Suite 2026.1.1, Quartus Prime Pro 26.1
**Budget used:** 5 of 5 Quartus compiles · 1,692 of ≤9,000 inferences · 5 programming cycles

---

## 1. Ladder

Latency is the device counter (CSR 576/580 `CLOCKS_ACTIVE`), never host wall clock.
"Single-stream" is the **pinned steady-state** value after the per-programming settling
ramp; "burst" is the **marginal** job from a least-squares fit of `ticks(N)` over queue
depths 1..32. Accuracy is the MLPerf Tiny **ic01** 200-image subset
(`perf_samples_idxs.npy`); the phase-12 baseline scored 84.00 % on it and 86.33 % on the
full 10,000-image test set.

| # | Configuration | dla_clk / ddr_clk | Single-stream | fps | Burst (marginal) | fps | ic01 | Timing | Verdict |
|---|---|---|---:|---:|---:|---:|---:|---|---|
| — | **baseline** k16c8, params in pseudo-DDR | 300 / 100 MHz | 527.57 µs | 1 895.5 | 503.65 µs | 1 985.5 | 84.00 % | met | reference |
| 1 | k16**c16** + `enable_on_chip_parameters` | 300 / 100 MHz | **295.73 µs** | **3 381.5** | — | — | **10.00 %** | met | **REJECT — wrong answers** |
| 2 | k16c8 + `enable_on_chip_parameters` | 300 / 100 MHz | 495.80 µs | 2 017.0 | — | — | **10.00 %** | met | **REJECT — wrong answers** |
| 3 | k16c8, DDR params | **375** / 100 MHz | 433.01 µs | 2 309.4 | 410.19 µs | 2 437.9 | 84.00 % | **Min Pulse Width −0.250 ns** | **REJECT — timing** |
| 4 | k16c8, DDR params | **340** / 100 MHz | 471.94 µs | 2 118.9 | 449.25 µs | 2 225.9 | 84.00 % | met | pass |
| 5 | k16c8, DDR params | **340 / 130.769 MHz** | **459.05 µs** | **2 178.4** | **440.39 µs** | **2 270.7** | 84.00 % | met | **BEST** |

Speed-up of the best passing build over the baseline: **1.149× single-stream, 1.144×
pipelined.** Intermediate queue depths on the winning build: depth 4 → 449.94 µs/job
(2 222.5 fps), depth 8 → 442.79 µs/job (2 258.4 fps); the fit is saturated by depth 8.

`ticks(N) = 57 590·N + 2 439` on the winning build (residual 0 at N = 16), i.e. a fixed
burst prologue of 2 439 ticks = 18.65 µs and nothing else — queueing is worth 4.2 %, the
same small constant the earlier dissection found.

---

## 2. What worked

**Raising `dla_clk` is the only lever that paid, and it pays almost linearly.**
Fitting the two clean data points gives

```
job_µs = 141 840 / f_dla(MHz) + 54.77 µs
```

— 141 840 dla-domain cycles plus a fixed 54.77 µs that lives in the ddr_clk domain. The
model predicted compile 4 at 471.95 µs against 471.94 µs measured and compile 5 at
459.1 µs against 459.05 µs measured, so it is now a reliable planning tool.

**Retiming really does follow the constraint.** At a 300 MHz ask the design closed at
Fmax 330 MHz; asked for 375 it closed at 377.36 MHz. The apparent ceiling was a
constraint artifact every time, exactly as phases 10–11 suggested.

**Raising `ddr_clk` is worth taking once `dla_clk` is maxed.** 100 → 130.769 MHz shrank
the fixed term from 54.77 µs to 41.9 µs and bought 2.7 % — small alone, but free once the
core clock has stopped scaling. It closed with 1.607 ns of setup slack to spare because
the 100 MHz domain was already achieving 7.23 ns.

**Correctness held perfectly on every clock rung.** The synthetic gate returned the pinned
logits `[-17.188, -12.719, -1.891, -3.781, -25.438, -22.0, +1.375, -22.344, -3.609, -22.0]`
**bit-identical** at 340 and 375 MHz and at both ddr_clk settings, the 205,056-byte
parameter image verified at FNV-1a `0x191007b5` on every programming, and ic01 accuracy was
84.00 % (168/200) on all three passing builds — the same images, the same confusion matrix.

---

## 3. What did not work

### 3.1 `enable_on_chip_parameters` is broken in this configuration — the headline negative

Compile 1 was the plan's centrepiece: `c_vector` 8 → 16 doubles the PE array to 256
MAC/cycle, and moving the parameters into on-chip ROM is the only way to afford the M20K.
It **fitted with room to spare and ran 1.78× faster**, and it was completely wrong.

| | measured |
|---|---|
| Fit | 23 543 / 34 000 ALM (69 %), **178 / 262 M20K (68 %)**, 32 / 276 DSP |
| Timing | met, dla setup +0.268 ns, Fmax 326.26 MHz |
| Latency | 295.73 µs/job — **1.784× the baseline** |
| Accuracy | **10.00 %** = chance |

The failure signature was unambiguous: **all 200 different ic01 images returned
byte-identical logits.** Diagnosis on the board (`build/fpga_ai/probe_c/diag13.tcl`,
`diag13b.tcl`) ruled out everything host-side and everything structural:

- host readback of `dla_io0` is byte-exact, so the input reaches the RAM;
- the feature reader issues exactly 1 024 ddr_axi beats per job — the whole 32 768 B
  tensor — and `DMA→InputFeeder` shows 1 024 transactions, so it is fetched;
- `InputFeeder→Sequencer` = 75 132 txn/job against a padded-issue model of 56 324, a
  ratio of 1.334 versus the baseline's 138 912/103 432 = 1.343 — **the execution geometry
  is correct**, so the config stream is fine;
- CSR 532 `config_range_minus_two` is *ignored entirely* in OCP mode (swept 2 187…2 433,
  zero effect) — the config comes from the ROM with its own length;
- the Fitter RAM Summary names every `ddrfree_*.mif`, mapped into
  `pe_array_system|gen_ddr_free_fbs_scratchpad.scratchpad|…|filter_mantissa_ram` at
  1024 × 140 with content matching the MIF exactly, so the ROMs are present, correctly
  shaped and correctly initialised;
- a zero input and the real stimulus give identical logits, and that shared answer does
  **not** match the golden CPU model evaluated on a zero input — so the core is not
  "computing correctly on zeros"; the weights are dead.

**Compile 2 settled it.** k16**c8** + OCP is byte-identical to the proven baseline arch
except `enable_on_chip_parameters` (plus the two depths the compiler then forces:
`filter_depth` 512 → 702, `config_cache_depth` 256 → 2433) — and it returned
**the same constant logits, to the last digit, at a completely different core shape**:

```
36.062  35.500  37.500  38.125  34.031  36.094  37.938  37.625  37.344  34.656
```

A result independent of both the input *and* the compute configuration can only be the
parameter path delivering nothing.

**Root cause.** All five vendor example architectures that set
`enable_on_chip_parameters` also set `disable_external_memory` **and** both stream
interfaces — `AGX7_Streaming_ocp_Ddrfree*.arch`. There is no supported example of on-chip
parameters combined with the DDR feature path, which is exactly what this build does.
`enable_on_chip_parameters` alone stops the DMA from writing the filter scratchpad but
evidently does not arm the ROM-backed read path, so the PE array multiplies by zero. The
combination is accepted silently by `dla_compiler` and by `dla_create_ip`, produces a
design that fits, closes timing, runs, and reports `diag = 0` — and is wrong.

### 3.2 `c_vector = 16` is therefore unreachable on this device

k16c16 with parameters in pseudo-DDR needs roughly **275 M20K against the 262 available**:
~150 for the IP (extrapolated from the 157 measured for the OCP variant, less the ~7 the
2433-deep config ROM adds), 104 for the 207 872 B parameter image at the mixed-width
64/32 rate of 4 M20K per 8 KiB, 20 for the 33 280 B I/O buffer, 1 for `jtag_master`. Two
independent routes to the estimate agree, and the earlier probe's calibrated model said
297. The 80 %-efficient parameter RAM is the whole problem and no arch knob recovers 13+
blocks. So on-chip parameters are *mandatory* for `c_vector = 16`, and they do not work.

### 3.3 375 MHz runs correctly but is not shippable

Compile 3 met setup (+0.016 ns), hold, recovery and removal, produced bit-identical logits
and 84.00 % accuracy over 200 images plus 225 probe jobs — and still must be rejected:

```
Minimum Pulse Width  outclk1  slack -0.250 ns  TNS -5444.523  32 597 failing end points  Type: Min Period
```

**Type = Min Period with essentially every register failing** is a silicon limit, not a
placement artifact. Slack −0.250 ns at a 2.6667 ns period puts the required minimum period
at 2.9167 ns, i.e. **342.86 MHz — a hard ceiling for core logic on this part at this speed
grade and corner.** Quartus's own Restricted Fmax agrees to two decimals (342.94 MHz) and
reported the identical figure on the 340 MHz build. No amount of retiming moves it.

340 MHz was chosen to sit just under it and lands at +0.025 ns of Min-Pulse-Width slack —
predicted before the compile from 2.9412 − 2.9167, and measured exactly.

---

## 4. Best configuration

**`build/fpga_ai/phase13_c5_k16c8_340_131mhz/`** — SOF sha256
`d983a0fa78d5bd9a3de356d252456619cee812854037e41f0f3e2aaff7aea2b1`

| | |
|---|---|
| Arch | `resnet8_agx3_int8_k16c8.arch` — k_vector 16, c_vector 8, FP12AGX (int8 DSP tensor mode), stream_buffer 8192, exit_fifo 1024, scratchpad 512/512, pool k1, `enable_round_clamp` |
| Compute | 128 MAC/cycle, **24 / 276 DSP (9 %)** |
| Clocks | **dla_clk 340.000 MHz**, **ddr_clk 130.769230 MHz** — IOPLL VCO 1700 MHz, M 68, N 1, C0 13, C1 5 (read out of the generated wrapper, not the `.ip`) |
| Utilisation | 24 024 / 34 000 ALM (71 %) · **254 / 262 M20K (97 %)** · 24 / 276 DSP (9 %) · 1 / 11 PLL |
| Timing | met everywhere — setup +0.157 / +1.607 ns, hold +0.001 / 0.000 ns, MPW +0.025 / +3.143 ns |
| Parameters | 205 056 B baked into `dla_par0/1/2` MIFs, verified on-board at FNV-1a `0x191007b5` |
| **Single-stream** | **459.05 µs = 2 178.4 inferences/s** (60 029 ticks × 7.64706 ns, pinned) |
| **Pipelined** | **440.39 µs/job = 2 270.7 inferences/s** (marginal job, saturated at queue depth 8) |
| **Accuracy** | **84.00 % ic01** (168/200), synthetic gate bit-identical to the pinned vector |

Host-side note: the CSR 576/580 tick is **7.6471 ns**, not 10 ns. It is not
self-describing, so `tools/run_cifar10_jtag.py` must be run with
`--config build/fpga_ai/phase13_work/runner_340_131.json`, which overrides
`latency_counters[0].hz` to 130 769 230. Without it every latency reads 30.8 % high and
entirely plausible.

---

## 5. Remaining headroom

**On `dla_clk`: none.** 340 MHz is within 0.9 % of the device's 342.86 MHz Min-Period
limit. The remaining 0.8 % is not worth a compile.

**On `ddr_clk`: ~1 %.** The fixed term is down to 41.9 µs of a 459 µs job. The domain's
Fmax is 138.35 MHz, so 141.67 MHz (VCO 1700 / C0 12) is out of reach; there is no legal
rung between 130.769 and the ceiling.

**On queueing: none left.** Saturated at depth 8; worth 4.2 % and already counted.

**On width — this is where the remaining 1.8× lives, and it is blocked by one bug.**
Compile 1 measured **295.73 µs/job (3 381 fps, 1.784×)** with 84 M20K and 31 % of the ALMs
still free. Everything about that build is right except that the parameter ROMs do not
feed the PE array. Two ways forward, in order of cost:

1. **Ask Altera whether `enable_on_chip_parameters` is supported without
   `disable_external_memory`.** The evidence package is complete and small: two
   architectures, two shapes, one identical constant output vector, ROMs provably
   initialised in the fit report. If it is a supported combination this is a tool bug with
   a large payoff; if it is not, the answer costs nothing.
2. **Go to the fully supported corner** — `enable_on_chip_parameters` +
   `disable_external_memory` + input and output streaming, matching
   `AGX7_Streaming_ocp_Ddrfree.arch`. This is the configuration the vendor validates. It is
   a substantial project: it deletes the descriptor/CSR I/O path this system is built
   around and moves onto the streaming datapath, which is where this project's earlier
   xbar → output-streamer bug lived. But it is the only route to `c_vector = 16`, and
   `c_vector` is the one shape ResNet-8 can actually use (13.2 vs 7.05 fps/MHz).

**Not worth pursuing:** eliminating the parameter re-fetch (the dissection already showed
it is ≤5 %, and compile 2 confirmed it directly — deleting 100 % of the 205 KB per-job
fetch moved single-stream only 527.57 → 495.80 µs); widening `dla_par*` to 256-bit (same
reason); `k_vector` beyond 16 (ResNet-8's layers have at most 64 output channels, so a
wider `k_vector` idles — measured 4.82 fps/MHz at k64).

---

## 6. Artifacts

| Path | Contents |
|---|---|
| `build/fpga_ai/phase13_c5_k16c8_340_131mhz/` | **best build** — SOF, qsys, qsf, IOPLL `.ip` + generated wrapper, arch, param MIFs, runner config, ic01 report, probe script + log |
| `build/fpga_ai/phase13_c4_k16c8_340mhz/` | 340 / 100 MHz build, same contents |
| `build/fpga_ai/phase13_c3_k16c8_375mhz/` | 375 MHz build — correct on hardware but Min-Pulse-Width negative; kept as the evidence for the 342.86 MHz ceiling |
| `build/fpga_ai/phase13_c1_k16c16_ocp_300mhz/` | k16c16 + OCP — the 1.784× build that returns constants |
| `build/fpga_ai/phase13_c2_k16c8_ocp_300mhz/` | k16c8 + OCP — the isolation experiment that identified OCP as the cause |
| `build/fpga_ai/phase13_work/` | `rebuild_phase13_*.tcl`, `set_dla_clk.py`, runner configs, qsys/generate logs, backups |
| `build/fpga_ai/probe_c/diag13.tcl`, `diag13b.tcl` | on-board diagnosis of the OCP failure (+ `.log`) |
| `build/fpga_ai/probe_c/probe375.tcl`, `probe340.tcl`, `probe340_131.tcl` | steady-state + burst sweeps (+ `.log`) |
| `build/fpga_ai/bench/p13c{3,4,5}_ic01/` | 200-image accuracy runs (`report.txt`, `results.jsonl`, `events.jsonl`) |

### Tooling changes

`tools/run_cifar10_jtag.py` — the arch-dependent geometry was hard-coded module state; it
is now configurable, because phase 13 needed to run three different memory maps against
one runner:

- `Config.input_cvec` + `set_input_cvec()` — c_vector drives the input tensor layout
  (`element = (h·32 + w)·Cvec + c`) and hence the input region size; applied once at
  start-up rather than per call, so a half-converted run cannot silently pack garbage.
- `param_bytes == 0` now skips the parameter readback instead of reading an address that
  no longer decodes.
- `Config.load()` accepts `_`-prefixed keys as comments (JSON has none, and these files
  carry the address-map reasoning that makes them auditable). Every other unknown key is
  still rejected.
- the transport report's `input write (16 KiB)` label follows `INPUT_BYTES`.

`build/fpga_ai/phase13_work/set_dla_clk.py` — retunes `outclk0`/`outclk1` in the IOPLL
`.ip` and, more importantly, **asserts the result out of the generated wrapper**. Two
traps it exists to catch, both hit during this work:

1. The IOPLL does not error on an unreachable frequency — it warns and silently substitutes
   (312.5 MHz becomes 314.29 MHz). Only `out_clk_*_freq` in the generated `.v` is truth.
2. Every `qsys-generate` mints a *new* wrapper with a fresh hash suffix and leaves the old
   ones in place, so that directory accumulates one `.v` per setting ever built. Reading
   all of them reports a stale frequency — which made a *successful* edit look like a
   failure here, and would just as easily make a failed edit look successful.
