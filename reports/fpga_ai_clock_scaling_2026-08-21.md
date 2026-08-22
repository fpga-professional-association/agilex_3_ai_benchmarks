# Phase 11 — CoreDLA clock scaling to the practical ceiling (Arrow AXC3000, Agilex 3)

Date: 2026-08-21
Design: FPGA AI Suite 2026.1.1 CoreDLA, arch `V1_8x8_AGX3`, ResNet-8, pseudo-DDR parameters.
Device: A3CY100BM16AE7S (Agilex 3, **speed grade 7**), Quartus Prime Pro 26.1.0 Build 110.

## Headline

**The CoreDLA core clock closed and passed the board gate at 300.00 MHz.**

This overturns the Phase 10 assessment, which called 300 MHz "not plausible on
this device and design ... a firm conclusion, not a hedge". That assessment was
wrong, and the reason it was wrong is the most useful result of this phase:
**the 235.79 MHz "measured ceiling" was never a ceiling — it was the Fmax of a
netlist the fitter had only been asked to run at 200 MHz.**

| rung | constraint | achieved Fmax | setup slack | board gate |
|---|---|---|---|---|
| Phase 9 | 100 MHz | 123.29 MHz (outclk0) | +1.889 ns | PASS |
| Phase 10 | 200 MHz | 235.79 MHz | +0.759 ns | PASS |
| Phase 11 compile 1 | **225 MHz** | **247.10 MHz** | +0.397 ns | PASS |
| Phase 11 compile 2 | **300 MHz** | **312.50 MHz** | +0.133 ns | **PASS** |

Achieved Fmax rose monotonically with the constraint — 235.79 → 247.10 → 312.50
MHz — because Quartus reports `Info (332129): Detected timing requirements --
optimizing circuit to achieve only the specified requirements`. Every previous
Fmax figure was an artefact of the requirement it was compiled against, not a
property of the silicon.

Logits are **bit-for-bit identical across 100 / 200 / 225 / 300 MHz**, all five
cases. DLA-only throughput went 395 → 1,137 fps (2.88x vs Phase 9). End-to-end
went 166 → 301 fps, and is now **73 % Nios-I/O-bound**.

Two Quartus compiles were used of the three budgeted.

## 1. PLL / VCO configuration per build

`iopll_0` is in **basic mode** (`gui_en_adv_params = false`), so it re-solves
M / N / C from the *desired output frequencies* at generation time. Only two
parameters were changed per build, via
`ip-deploy --system-file=ip/NIOSV_lab/NIOSV_lab_iopll_0.ip --quartus-project=axc3000_top`:

```
--component-parameter=gui_output_clock_frequency1=<MHz>
--component-parameter=gui_output_clock_frequency_ps1=<ps>
```

Reference clock is **25 MHz** (`CLK_25M_C`, PIN_A7), N = 1 throughout.

| build | outclk1 target | M | C0 | C1 | VCO | **outclk0 actual** | **outclk1 actual** |
|---|---|---|---|---|---|---|---|
| Phase 10 | 200 MHz | 96 | 24 | 12 | 2400 MHz | **100.000 MHz** | **200.000 MHz** |
| Phase 11 compile 1 | 225 MHz | 72 | 18 | 8 | **1800 MHz** | **100.000 MHz** | **225.000 MHz** |
| Phase 11 compile 2 | 300 MHz | 96 | 24 | 8 | 2400 MHz | **100.000 MHz** | **300.000 MHz** |

Both outputs are exact integer divisions in every build — there is **no frequency
error on either domain**, and outclk0 never moved off 100.000 MHz. 225 MHz forced
a VCO change because 100 and 225 have no common VCO below 1800 MHz
(VCO must be an integer multiple of both; LCM(100, 225) = 900, and the
solver picked 1800). 300 MHz needed no VCO change: 2400 / 8 = 300 exactly.

### The trap, corrected

Phase 10 recorded that `ip-deploy` "leaves the derived `gui_divide_factor_c1`
and `..._ps1` at their 100 MHz values ... those had to be set explicitly — the
divider is what actually reaches hardware."

**That is half right and half wrong, and the wrong half matters.** `ip-deploy`
does leave `gui_divide_factor_c*`, `gui_multiply_factor` and `gui_vco_frequency`
stale — after this phase's 225 MHz deploy they still read 6 / 3 / 6 / 600.0, the
Phase 10 values. But in basic mode **those parameters are not inputs to
generation**: `set_physical_parameter_values` in
`altera_iopll_hw_validation.tcl` feeds the computation code the *actual output
frequencies*, and `map_fb_clk_m_div` / `map_out_clk_c_div` in
`altera_iopll_hw_generation.tcl` build the hardware counters from the solver's
answer. Setting `gui_divide_factor_c1` by hand was harmless in Phase 10 only
because the value happened to be consistent. At 225 MHz it would have been
actively wrong: the correct hardware C0 is 18, which is not expressible in the
GUI parameter's units.

**The reliable check is not the `.ip` — it is the generated RTL.** Every build
this phase was verified by reading the counters the fitter actually receives:

```
ip/NIOSV_lab/NIOSV_lab_iopll_0/altera_iopll_2110/synth/*.v
    .ref_clk_0_freq(32'd25000000)  .ref_clk_n_div(1)
    .fb_clk_m_div(72)  .vco_clk_freq(36'd1800000000)
    .out_clk_0_c_div(18)  .out_clk_0_freq(36'd100000000)
    .out_clk_1_c_div(8)   .out_clk_1_freq(36'd225000000)
```

and the matching SDC generated-clock ratios in `*_parameters.tcl`
(`outclk0 multiply_by 72 divide_by 18`, `outclk1 multiply_by 72 divide_by 8`),
which is what the Timing Analyzer constrains against. Both were confirmed on a
throwaway copy of the `.ip` *before* either project change, at zero compile cost.

VCO legality was not assumed either: the IP's own validation
(`gui_vco_frequency_validation`, which queries
`::quartus::pll::legality::get_legal_vco_range`) runs inside `ip-deploy`, and
reported `Able to implement PLL with user settings` for both 1800 MHz and
2400 MHz.

### Everything else in the clock/reset structure is unchanged from Phase 10

`iopll_0.outclk1 -> fpga_ai.dla_clk` and nothing else; `outclk0` still drives the
Nios V, mtime, the pseudo-DDR RAMs and `fpga_ai`'s `ddr_clk` / `irq_clk`. No SDC
changes, no false paths, no clock groups. The `.qsys` delta from Phase 10 is
**three numbers** (the outclk1 clock rate), once the regenerated IP instance
names are normalised.

## 2. Timing per compile, per domain

All figures are worst-case across corners, from `axc3000_top.sta.summary` and
`report_clock_fmax_summary`. `get_clock_fmax_info` returns an empty list in this
Quartus build, so `scripts/phase11_fmax.tcl` reads the Fmax panel directly.

| | Phase 9 (100) | Phase 10 (200) | **P11 c1 (225)** | **P11 c2 (300)** |
|---|---|---|---|---|
| outclk0 requested | 100.00 MHz | 100.00 MHz | 100.00 MHz | 100.00 MHz |
| outclk0 **Fmax** | 123.29 | 124.49 | **124.63** | **124.98** |
| outclk0 setup | +1.889 | +1.967 | +1.976 | +1.999 |
| outclk0 hold | +0.002 | +0.011 | +0.003 | +0.009 |
| outclk0 recovery / removal | +6.207 / +0.153 | +7.510 / +0.154 | +5.663 / +0.152 | +6.095 / +0.171 |
| outclk1 requested | — | 200.00 (5.000 ns) | **225.00 (4.444 ns)** | **300.00 (3.333 ns)** |
| outclk1 **Fmax** | — | 235.79 | **247.10** | **312.50** |
| outclk1 setup | — | +0.759 | **+0.397** | **+0.133** |
| outclk1 hold | — | +0.011 | +0.012 | **+0.000000** |
| outclk1 recovery / removal | — | +2.779 / +0.158 | +2.236 / +0.153 | +1.435 / +0.154 |
| outclk1 min pulse width | — | +1.837 | +1.528 | +0.417 |
| **TNS, every type** | 0.000 | 0.000 | **0.000** | **0.000** |
| ALMs | 20,680 (61 %) | 21,819 (64 %) | **21,997 (65 %)** | **22,118 (65 %)** |
| Registers | 44,251 | 44,418 | 44,324 | **49,402** |
| M20K | 262/262 (100 %) | 262/262 | 262/262 | 262/262 |
| DSP | 12/276 | 12/276 | 12/276 | 12/276 |
| PLLs | 1/11 | 1/11 | 1/11 | 1/11 |
| Compile wall time | — | 7 m 15 s | **7 m 44 s** | **7 m 18 s** |

Both Phase 11 compiles: `0 errors`, `Timing requirements were met`, no negative
slack of any type on any clock at any corner.

### Where the time actually goes, and why 300 worked

**At 225 MHz the critical path is still the encrypted activation round/clamp**,
exactly as Phase 10 found:

```
slack +0.397  u0|fpga_ai|...|aux_activation_inst|dla_aux_activation_group_inst
              |dla_aux_activation_lane_inst|dla_aux_activation_core_inst
              |gen_activations.gen_round_clamp_hw_block.round_clamp_hw_block_inst
              |gen_clamp_vectors[6].result_reg[6]
```

All 15 worst setup paths land in that block. Phase 10 concluded from this that
the block was an immovable limiter, since it is encrypted vendor RTL inside a
`round_clamp` the compiler *forces* on a FakeQuantize graph.

**At 300 MHz that path is gone from the top of the list entirely.** The new
critical path is the crossbar input encoder:

```
slack +0.133  u0|fpga_ai|...|xbar_inst|gen_single.gen_inp_pipe0[2].inp_enc_inst
              |gen_pipelined_ready.u_stage|r_data[0][32]
```

The mechanism is visible in the register count: **+5,078 registers** at 300 MHz
against 225 MHz (44,324 → 49,402) for **+121 ALMs**. `ALLOW_REGISTER_RETIMING`
is On by default for Agilex 3, and when finally given a requirement it could not
meet by placement alone, the retimer re-balanced the round/clamp datapath.
Encryption prevents *us* from pipelining that block; it does not prevent the
**retimer** from doing it, because retiming operates on the post-synthesis
netlist. That distinction is the whole reason the Phase 10 verdict was wrong.

### The one caveat worth flagging

At 300 MHz the worst **hold** slack on `dla_clk` is **exactly 0.000000 ns**
(full precision via `get_path_info -slack`, not the 3-decimal summary), on three
paths, all `BLOCK_INPUT_MUX_PASSTHROUGH` LAB-mux registers:

```
0.000000  ...|wa_pool_output_inst|gen_single.inp_pipe_inst|gen_pipelined_valid.u_skid|r_valid~BLOCK_INPUT_MUX_PASSTHROUGH_X51_Y42_N0_I14_dff
0.000000  ...|xbar_inst|gen_single.xbar_ctrl_fsm_curr_state.XBAR_CTRL_FSM_PROCESSING_INP_COUNT_ST~BLOCK_INPUT_MUX_PASSTHROUGH_X67_Y41_N0_I27_dff
0.000000  ...|interface_profiling_counters|GEN_PROFILING_COUNTERS[9].count_transaction_lo[25]~BLOCK_INPUT_MUX_PASSTHROUGH_X99_Y22_N0_I75_dff
```

Hold TNS is 0.000 and Quartus signs the build off, so this is *met*, not
violated — the fitter padded these paths to exactly the requirement and stopped,
which is its normal behaviour. It is nonetheless zero margin on the fast corner,
and it is the only number in this phase that would make me want a temperature
sweep before shipping. The 225 MHz build has +0.012 ns there and is the
conservative fallback rung if that margin is ever judged insufficient; it is
preserved complete in `build/fpga_ai/phase11_225mhz/`.

## 3. Board gate

Both rungs were programmed from their own timing-met SOF (byte-identical to the
snapshot) and gated identically. No timing-failing build was ever programmed —
neither Phase 11 compile failed timing.

| check | 225 MHz | 300 MHz |
|---|---|---|
| `synthetic_nhwc` → class | **6** | **6** |
| `synthetic_nchw` → class | 6 | 6 |
| raw FP16 logits vs Phase 9 / 10 | **bit-for-bit identical**, all 5 cases | **bit-for-bit identical**, all 5 cases |
| zero ≠ all-255 | `0xa8ec4c85` ≠ `0x74e53874` | `0xa8ec4c85` ≠ `0x74e53874` |
| 500-frame alternating sweep | `completed=500`, `VERDICT=input_varying` | `completed=500`, `VERDICT=input_varying` |
| sweep hashes vs Phase 9 / 10 | `0x51aeb737` / `0xe90bc0cd` — identical | `0x51aeb737` / `0xe90bc0cd` — identical |
| class histogram | 250 × 0, 250 × 6 | 250 × 0, 250 × 6 |
| diagnostics / irq error | `0x00000000`, no error bit | `0x00000000`, no error bit |
| `uart_dropped` | 0 | 0 |
| pseudo-DDR self-test | `errors=0` | `errors=0` |
| inferences this run (limit 2000) | 506 | 506 |
| **verdict** | **PASS** | **PASS** |

Raw FP16 logit codes, unchanged from Phase 9 at every clock:

```
zero            42e0 c4a4 c2e0 3580 4078 bae0 3cd0 c078 c0d0 c3e8
all255          4128 c230 3cd0 40d0 c020 bae0 0000 c420 c288 c4fc
synthetic_nhwc  cc4c ca5c bf90 c390 ce5c cd80 3d80 cd96 c338 cd80   -> class 6
synthetic_nchw  ccba cbd2 c478 bc20 ce3b cdc2 3580 cd96 c6e0 cdf9   -> class 6
```

**Logit equivalence statement:** across a 3x change in `dla_clk` (100 → 300 MHz)
with `ddr_clk` held at 100 MHz, every output bit of every test case is
unchanged, and 500 alternating frames hash identically. Any marginality in the
`dla_clk`↔`ddr_clk` synchronisers would have shown up as sporadic bit
differences somewhere in 500 frames at three different clock ratios. There were
none. This is the strongest available evidence that the Phase 10 decision to add
no SDC false paths — relying on the IP's own `dla_clock_cross_sync.sdc` — was
correct rather than merely lucky.

Captures: `reports/fpga_ai_phase11_225mhz_uart_2026-08-21.txt`,
`reports/fpga_ai_phase11_300mhz_uart_2026-08-21.txt`.

## 4. Performance

All wall-clock figures come from `mtime`, which stayed in the 100 MHz outclk0
domain (10 ns per tick) and is unaffected by the clock split. `DLA_CLOCK_HZ` in
`main.c` was updated per build (200 → 225 → 300 MHz) but is **not** the timebase
for any figure below — it only labels the capture.

| metric | 100 MHz (P9) | 200 MHz (P10) | **225 MHz** | **300 MHz** |
|---|---|---|---|---|
| mean DLA ticks / frame | 253,375 | 129,325 | **115,534** | **87,969** |
| mean DLA latency | 2.5337 ms | 1.2933 ms | **1.1553 ms** | **0.8797 ms** |
| **fps, DLA only** | 395 | 773 | **865** | **1,136** |
| fps, DLA + tail | 393 | 769 | 861 | 1,128 |
| mean frame ticks | 602,079 | 372,787 | 358,988 | **331,448** |
| **fps, end-to-end** | 166 | 268 | **278** | **301** |
| non-DLA ticks / frame | 348,704 | 243,462 | 243,454 | **243,479** |
| non-DLA share of frame | 58 % | 65 % | 68 % | **73 %** |

(The 100 MHz row predates the Phase 10 input-copy fix, which is why its non-DLA
figure is higher; the three later rows share identical firmware apart from
`DLA_CLOCK_HZ`.)

### The clock is doing exactly what it should

Non-DLA time is **243,462 / 243,454 / 243,479 ticks** at 200 / 225 / 300 MHz — a
spread of 25 ticks in 243 k (0.01 %). The Nios path is genuinely untouched by the
clock change, which is what makes the DLA numbers trustworthy.

Fitting the DLA job time to `ticks(f) = A·(100/f) + B` by least squares over all
four rungs:

- **A = 248,109 ticks** — work that scales with `dla_clk`
- **B = 5,266 ticks (52.7 µs)** — fixed overhead that does not

| f | measured | model | error |
|---|---|---|---|
| 100 MHz | 253,375 | 253,375 | 0 |
| 200 MHz | 129,325 | 129,321 | +4 |
| 225 MHz | 115,534 | 115,537 | −3 |
| 300 MHz | 87,969 | 87,969 | 0 |

The model reproduces every point to within **4 ticks (0.004 %)**. So 98.0 % of
the DLA job scales perfectly with the core clock, and the 52.7 µs residue is
descriptor fetch and completion signalling in the `ddr_clk` domain, which stayed
at 100 MHz. Measured speedups are 1.959x / 2.193x / 2.880x against ideal 2.00 /
2.25 / 3.00 — the shortfall is entirely that fixed term, and it grows as a share
of a shrinking job, exactly as the model predicts. This is also the in-silicon
confirmation that `dla_clk` really is running at 3x: nothing else explains a
2.88x latency reduction measured on an untouched 100 MHz timebase.

(CSR 636 `CORE_CLOCKS_ACTIVE` remains unusable — it reads 0 at all times in this
configuration, as in Phase 10. The latency ratio is the stronger witness anyway,
being a measurement of real work rather than of a counter.)

## 5. Where the ceiling actually is

**Not established.** 300 MHz was the target, it closed with +0.133 ns and passed,
and the compile budget was spent. What can be said factually:

- **Achieved Fmax at 300 MHz is 312.50 MHz**, so ≈ 312 MHz is reachable with the
  current netlist and no further work. Whether the pattern continues — each
  tighter constraint unlocking more retiming — is untested. It held for three
  consecutive rungs.
- **Every constraint on the "why not higher" list from Phase 10 is still true,
  and none of them bound at 300 MHz**: speed grade 7, M20K pinned at 262/262
  (100 %), `round_clamp` forced by the FakeQuantize graph and unmodifiable, and
  the vendor's own Agilex 3 reference platform running `dla_clk` at 150 MHz. We
  are now at **2x the vendor's reference clock for this family**, on a
  100 %-M20K-full design, on the slowest speed grade.
- The Phase 10 reasoning failed on a specific point that is worth naming: it
  treated "the critical path is in encrypted RTL we may not modify" as
  equivalent to "the critical path cannot be improved". Register retiming
  improved it anyway, because retiming does not need source access. Encryption
  blocks *authoring*, not *optimisation*.
- ALM cost of the whole 100 → 300 MHz climb is **+1,438 ALMs (+7.0 %)** and
  **+5,151 registers (+11.6 %)**, with **zero** extra M20K or DSP. Area was never
  the binding constraint.

### The next lever is not the clock

End-to-end throughput is now **73 % Nios-I/O-bound**: 243,479 of 331,448 ticks
per frame are the input copy and output readback, against 87,969 ticks of DLA.

The arithmetic is decisive. An **infinitely fast** DLA — zero inference time —
would take end-to-end from 301 fps to 411 fps, a 1.36x ceiling on everything
further clock work could ever buy. Removing the input-copy bottleneck is worth
more than that, and it is a bounded piece of work: a DMA engine or a wider burst
path for the 16 KiB input tensor, replacing a per-element Nios loop. **Input DMA
is the correct next phase**, and it is worth more than another clock rung even
if that rung were free.

## 6. Artifacts

- `build/fpga_ai/phase11_300mhz/` — **the passing 300 MHz state**: SOF
  (byte-identical to what was programmed), `NIOSV_lab.qsys`, `axc3000_top.qsf`,
  `NIOSV_lab_iopll_0.ip`, `.sta.summary`, `.fit.summary`, `phase11_fmax.rpt`,
  `phase11_worst_setup.rpt`, final ELF, `main.c`, UART capture.
- `build/fpga_ai/phase11_225mhz/` — the passing 225 MHz state, complete and
  independently gated; the conservative fallback rung.
- `build/fpga_ai/phase10_200mhz/`, `build/fpga_ai/phase9_good_100mhz/` — prior
  recoverable states, untouched.
- `reports/fpga_ai_phase11_225mhz_uart_2026-08-21.txt`,
  `reports/fpga_ai_phase11_300mhz_uart_2026-08-21.txt` — board captures.
- `fpga/axc3000_mlperf/phase11_compile1_225mhz.log`,
  `fpga/axc3000_mlperf/phase11_compile2_300mhz.log` — compile logs.
- Scripts added: `scripts/phase11_fmax.tcl` (Fmax panel — `get_clock_fmax_info`
  is empty in this build), `scripts/phase11_precision.tcl` (full-precision slack,
  needed to adjudicate the 0.000 ns hold).

## 7. Process notes

- **`ip-deploy` writes a *new* `.ip` into the current working directory**, it
  does not edit `--system-file` in place, and the file it writes **drops
  `bonusData` and `lockedInterfaceDefinition`**. Both were spliced back from the
  previous file (with the cached outclk1 rate corrected) so the only delta per
  build is the frequency. It also did not inject a doubled `IP_FILE` path into
  the `.qsf` this time — the Phase 10 sighting was probably an artefact of the
  working directory it was run from.
- **`rebuild_fpga_ai_instance.tcl` mints a new IP generation every run and the
  `.qsf` keeps *both*.** It happened on both compiles this phase
  (`_fpga_ai_5`→`_6`→`_7`, `_dla_*_0`→`_1`→`_2`). Four stale `IP_FILE` lines had
  to be removed each time; the check is that the `.qsf` `IP_FILE` set exactly
  equals the set `NIOSV_lab.qsys` references (12 entries).
- **Verify PLL changes in the generated `.v`, never in the `.ip`.** See §1.
- Editing the `.qsf` with Python text mode silently converts CRLF to LF; convert
  back, or the file diffs entirely against its snapshot.
- `scripts/regen_system.cmd` (rebuild instance → split dla_clk → generate) is
  rerun-safe and was used unchanged apart from removing a hardcoded "200 MHz"
  from an echo. `split_dla_clock.tcl`'s save gate reported
  `verified: fpga_ai.dla_clk is driven by iopll_0.outclk1 only` on both runs.
- PowerShell mangles `--component-parameter=a=b`; use the `--%` stop-parsing
  token. `Tee-Object` writes UTF-16 — UART captures need converting to UTF-8.

## 8. Working tree state

The working tree is at the **300 MHz passing configuration** — the highest gated
rung, matching `build/fpga_ai/phase11_300mhz/` exactly (`.qsys`, `.qsf`,
`NIOSV_lab_iopll_0.ip`, generated RTL, `main.c` with
`DLA_CLOCK_HZ = 300000000u`, ELF, and the SOF in `output_files/`). The board is
currently programmed with that SOF.

Nothing was committed to git.
