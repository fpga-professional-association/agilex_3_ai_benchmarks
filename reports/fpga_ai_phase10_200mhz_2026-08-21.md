# Phase 10 — CoreDLA core clock 100 MHz → 200 MHz (Arrow AXC3000, Agilex 3)

Date: 2026-08-21
Design: FPGA AI Suite 2026.1.1 CoreDLA, arch `V1_8x8_AGX3`, ResNet-8, pseudo-DDR parameters.
Device: A3CY100BM16AE7S (Agilex 3, speed grade 7), Quartus Prime Pro 26.1.0 Build 110.

## Headline

**dla_clk closed at 200.00 MHz with positive slack on every analysis type, and the
board re-passed the correctness gate with logits bit-for-bit identical to the
Phase 9 100 MHz build.** Achieved DLA-domain Fmax is **235.79 MHz**.

DLA-only throughput went 394 → 773 fps (1.96x). End-to-end went 166 → 268 fps
(1.61x) after also fixing the Nios input-copy bottleneck.

One Quartus compile was used of the three budgeted.

## 1. Clock / reset wiring changes (exact deltas)

### PLL — `ip/NIOSV_lab/NIOSV_lab_iopll_0.ip`

Modified with `ip-deploy --system-file=... --component-parameter=...` (not by hand):

| parameter | before | after |
|---|---|---|
| `gui_number_of_clocks` | 1 | 2 |
| `gui_output_clock_frequency1` | 100.0 | **200.0** |
| `gui_output_clock_frequency_ps1` | 10000.0 | **5000.0** |
| `gui_divide_factor_c1` | 6 | **3** |
| `gui_vco_frequency` | 600.0 | 600.0 (unchanged) |
| `gui_divide_factor_c0` | 6 | 6 (unchanged) |
| `gui_reference_clock_frequency` | 25.0 | 25.0 (unchanged) |

The 600 MHz VCO already supported both targets: ÷6 = 100 MHz, ÷3 = 200 MHz. No
VCO change was needed, so outclk0 is bit-identical to Phase 9.

**Trap:** `ip-deploy` set `gui_output_clock_frequency1` but left the *derived*
`gui_divide_factor_c1` and `..._ps1` at their 100 MHz values. Those had to be set
explicitly — the divider is what actually reaches hardware.

**Trap:** `ip-deploy` also injected a bogus `IP_FILE` entry with a doubled path
(`ip/NIOSV_lab/ip/NIOSV_lab/NIOSV_lab_iopll_0.ip`) into the `.qsf`. Removed.

### System — `NIOSV_lab.qsys`

Exactly one connection changed (`scripts/split_dla_clock.tcl`):

```
- iopll_0.outclk0 -> fpga_ai.dla_clk
+ iopll_0.outclk1 -> fpga_ai.dla_clk
```

Everything else is untouched: `iopll_0.outclk0` still drives `niosv_m`,
`jtag_uart`, `sysid`, `onchip_memory`, `dla_par0/1`, `dla_io`, and — critically —
`fpga_ai.ddr_clk` and `fpga_ai.irq_clk`.

Why only `dla_clk` moves: the component's own declarations bind `csr_axi` and
`ddr_axi` to `ddr_clk` and `irq_level` to `irq_clk`. **No externally visible
interface is associated with `dla_clk`** — it is purely the internal datapath
clock. Platform Designer therefore inserted no clock-crossing bridge and the
generated interconnect is unchanged from Phase 9.

### Reset — no manual change required

`dla_resetn` is declared `associatedClock = dla_clk`. Platform Designer
automatically created a second reset controller on the new domain:

```
iopll_0:outclk_1 -> [fpga_ai:dla_clk, rst_controller_001:clk]
rst_controller_001:reset_out -> fpga_ai:dla_resetn
```

Both reset sources (`reset_in.out_reset`, `reset_release.ninit_done`) feed it, as
before. The DDR side keeps `rst_controller_003` on outclk0.

### SDC — deliberately unchanged

No false paths, no clock groups were added. Verified from the IP sources that
this is correct rather than assumed:

- The IP ships `dla_clock_cross_sync.sdc` (already sourced by this project) which
  cuts the first synchronizer stage of `dla_clock_cross_full_sync`,
  `dla_clock_cross_half_sync` and `dla_cdc_reset_async` by wildcard.
- `dla_acl_dcfifo.sv:66` states outright: *"Unlike dcfifo, we do not need to
  supply an SDC file"* — its gray-coded pointer crossings are built from
  `dla_clock_cross_full_sync` instances (lines 296/309/421/434), which the
  shipped SDC already covers.
- The vendor's own Agilex 3 platform SDC contains clock groups only for JTAG,
  nothing for dla/ddr — confirming the IP's SDC is sufficient on its own.

Adding blanket false paths would only have masked real violations. None were
needed: every crossing met timing as constrained.

## 2. Area / effort changes

**The brief's planned M20K relief does not exist.** `onchip_memory` is
**64 KiB, not 512 KiB** (`onchip_memory.s1 start=0x0 end=0x10000`), and the
firmware already occupies ~47 KiB of it. It was left alone. M20K stayed pinned at
262/262 (100%) for both builds.

QSF additions (the only two):

```tcl
set_global_assignment -name OPTIMIZATION_MODE "Superior Performance With Maximum Placement Effort"
set_global_assignment -name FITTER_EFFORT "Standard Fit"
```

`ALLOW_REGISTER_RETIMING` was already `On` by default for Agilex 3 — no change.
SignalTap was already disabled and the qsf already pruned of the previous
generation's stale IP entries; the instance rebuild minted a new generation
(`_fpga_ai_5`, `_dla_par0_0`, `_dla_par1_0`, `_dla_io_0`) and the retired
`_fpga_ai_4` / `_dla_par0` / `_dla_par1` / `_dla_io` entries were removed. The
qsf's `IP_FILE` set now matches the qsys's referenced set exactly.

## 3. Timing — per domain

Pre-compile probe of the **Phase 9** snapshot (`scripts/probe_dla_fmax.tcl`, no
compile): the 123 MHz Phase 9 ceiling was **not** the DLA's. Its worst path was
`dla_par0` M20K → `niosv_m` LSU. Restricting to paths with both endpoints inside
`fpga_ai` gave 146.6 MHz — but that was an *unconstrained* figure, with 3.181 ns
of unused slack the fitter had no reason to reclaim.

| | Phase 9 (100 MHz) | Phase 10 compile 1 (200 MHz) |
|---|---|---|
| outclk0 requested | 100.00 MHz (10.000 ns) | 100.00 MHz (10.000 ns) |
| outclk0 **Fmax** | 123.29 MHz | **124.49 MHz** |
| outclk0 setup slack | +1.889 ns | **+1.967 ns** |
| outclk0 hold slack | +0.002 ns | +0.011 ns |
| outclk0 recovery / removal | +6.207 / +0.153 ns | +7.510 / +0.154 ns |
| outclk1 requested | — | 200.00 MHz (5.000 ns) |
| outclk1 **Fmax** | — | **235.79 MHz** |
| outclk1 setup slack | — | **+0.759 ns** |
| outclk1 hold slack | — | +0.011 ns |
| outclk1 recovery / removal | — | +2.779 / +0.158 ns |
| outclk1 min pulse width | — | +1.837 ns |
| ALMs | 20,680 / 34,000 (61%) | **21,819 / 34,000 (64%)** |
| Registers | 44,251 | 44,418 |
| M20K | 262 / 262 (100%) | 262 / 262 (100%) |
| DSP | 12 / 276 | 12 / 276 |
| PLLs | 1 / 11 | 1 / 11 |
| Compile wall time | — | 7 min 15 s |

**All TNS = 0.000.** No violated paths on either domain. Cost of the 2x clock was
+1,139 ALMs (+5.5%), from retiming and register duplication; no extra M20K or DSP.

Compiles used: **1 of 3.** Compiles 2 and 3 were not needed and were not run.

The 146.6 MHz probe under-predicted the result by a wide margin, which is the
expected behaviour of an unconstrained measurement: with a real 5 ns constraint
and maximum placement effort the fitter reclaimed far more than the probe implied.
The probe was still worth running — it was cheap (9 s) and it correctly
identified that the DLA was *not* the Phase 9 limiter.

## 4. Correctness gate at 200 MHz — PASS

Capture: `reports/fpga_ai_phase10_200mhz_fastcopy_2026-08-21.txt`
(first 200 MHz run, before the copy fix: `reports/fpga_ai_phase10_200mhz_uart_2026-08-21.txt`)

| check | result |
|---|---|
| `synthetic_nhwc` → class | **6** ✓ |
| logits vs Phase 9 100 MHz | **bit-for-bit identical**, all 5 cases ✓ |
| zero ≠ all-255 | `0xa8ec4c85` ≠ `0x74e53874` ✓ |
| 500-frame alternating sweep | `completed=500`, `VERDICT=input_varying` ✓ |
| sweep hashes vs Phase 9 | `0x51aeb737` / `0xe90bc0cd` — identical ✓ |
| class histogram | 250 × class 0, 250 × class 6 ✓ |
| diagnostics / irq error | `0x00000000`, no error bit ✓ |
| `uart_dropped` | 0 ✓ |
| pseudo-DDR self-test | `errors=0` ✓ |
| inferences this run | 506 (limit 2000) ✓ |

Raw FP16 logit codes, identical between the 100 MHz and 200 MHz builds:

```
zero            42e0 c4a4 c2e0 3580 4078 bae0 3cd0 c078 c0d0 c3e8
all255          4128 c230 3cd0 40d0 c020 bae0 0000 c420 c288 c4fc
synthetic_nhwc  cc4c ca5c bf90 c390 ce5c cd80 3d80 cd96 c338 cd80   -> class 6
synthetic_nchw  ccba cbd2 c478 bc20 ce3b cdc2 3580 cd96 c6e0 cdf9   -> class 6
```

Bit-exact agreement across a 2x clock change is the strongest available evidence
that the CDC is sound: any synchronizer marginality would have shown up as
sporadic bit differences across 500 frames. There were none.

## 5. Performance

All wall-clock figures come from `mtime`, which stayed in the 100 MHz outclk0
domain and is therefore unaffected by the split (10 ns per tick).

| metric | Phase 9 100 MHz | Phase 10 200 MHz | Phase 10 + fast copy | vs Phase 9 |
|---|---|---|---|---|
| mean DLA ticks / frame | 253,375 | 129,324 | **129,325** | **0.51x** |
| mean DLA latency | 2.5338 ms | 1.2932 ms | **1.2933 ms** | |
| **fps DLA-only** | 394 | 773 | **773** | **1.96x** |
| fps DLA + tail | 393 | 769 | 769 | 1.96x |
| mean frame ticks | 602,079 | 522,905 | **372,787** | 0.62x |
| **fps end-to-end** | 166 | 191 | **268** | **1.61x** |
| non-DLA ticks / frame | 348,704 | 393,581 | **243,462** | 0.70x |

### Measured clock ratio

`253,375 / 129,325 = ` **1.959x**, measured on the untouched 100 MHz mtime. This
is the in-silicon confirmation that dla_clk really runs at 2x.

The intended witness — CSR 636 `CORE_CLOCKS_ACTIVE` — turned out to be
**unusable: it reads 0 at all times** in this CoreDLA configuration. CSR 576
`CLOCKS_ACTIVE` does count, but its delta tracks mtime 1:1 (129,009 vs 129,044
ticks for one inference), which shows CSR 576 lives in the **ddr_clk** domain.
That is itself a useful negative result: it independently confirms ddr_clk
stayed at 100 MHz across the split. The DLA latency ratio is a stronger witness
than a counter ratio anyway, being an end-to-end measurement of real work.

### Input-copy optimisation — and a correction

The brief suggested switching the input copy to 32-bit word writes for "2x fewer
bus transactions". Done and measured, it was a **regression**: transactions fell
4x (16384 byte stores → 4096 word stores) but the non-DLA path got **12.9%
slower** (348,704 → 393,581 ticks). The loop is dominated by per-element
`uint8_to_half()`/`input_value()` work, not by bus transactions, and the extra
per-element branch cost more than the saved stores.

The real win was to stop touching the Cvec padding channels. Channels 3..7 of the
8-wide Cvec are zero for every frame forever, so they are now written **once** by
`ddr_zero_input()` and the per-frame copy only refreshes the 2 words per pixel
that hold image data. Per frame: 8192 loop iterations → 2048, and 16384 byte
stores → 2048 word stores. Non-DLA time fell to 243,462 ticks — **30% faster
than Phase 9**, and end-to-end went 191 → 268 fps.

The end-to-end path is still Nios-copy-bound: 243,462 of 372,787 ticks (65%) are
input copy plus output readback, against 129,325 ticks of DLA.

## 6. Assessment for a 300 MHz phase

**300 MHz is not plausible on this device and design.** This is a firm
conclusion, not a hedge.

Evidence:

1. **Measured ceiling is 235.79 MHz** for this exact netlist, already produced
   under `Superior Performance With Maximum Placement Effort`. 300 MHz demands a
   further **+27%**, from a build that has already had the fitter's best effort.
2. **The critical path is inside encrypted vendor RTL we may not modify.** All
   15 worst setup paths land in the same block:
   `dla_top_inst|aux_activation_inst|dla_aux_activation_group_inst|dla_aux_activation_lane_inst|dla_aux_activation_core_inst|gen_activations.gen_round_clamp_hw_block.round_clamp_hw_block_inst|gen_clamp_vectors[3].result_reg[*]`
   — the activation round/clamp datapath, at +0.759 ns. We cannot pipeline it.
3. **`round_clamp` is one of the two forced arch deviations.** It is enabled only
   because the compiler *requires* it for a FakeQuantize graph. So the frequency
   limiter is a block the quantization strategy forces on us.
4. **M20K is at 262/262 (100%).** The fitter has essentially no freedom to
   relocate memories to shorten paths, and no relief is available: the
   `onchip_memory` shrink assumed in the brief does not exist (it is already
   64 KiB with ~47 KiB used).
5. **Speed grade 7** — this is not a fast part.
6. **The vendor's own Agilex 3 reference runs dla_clk at 150 MHz.** At 200 MHz we
   are already 33% above the vendor's own reference point for this family.

What it would actually take to go higher:

- **~225 MHz looks reachable** and is the honest next rung: it sits inside the
  measured 235.79 MHz ceiling with ~5% margin. It needs a VCO change (e.g. 900
  MHz with ÷4 = 225 and ÷9 = 100, subject to the IOPLL's legal VCO range), so
  outclk0 must be re-verified at 100 MHz. Expect roughly 1.12x more DLA
  throughput — modest.
- **Beyond ~240 MHz requires changing something we froze this phase**: a
  quantization approach that does not force `enable_round_clamp`, a smaller arch
  that frees M20K and gives the placer room, a faster speed grade, or a vendor IP
  revision that pipelines the activation round/clamp.
- **The better target is not clock at all.** The DLA is now 1.29 ms of a 3.73 ms
  frame; 65% of end-to-end time is the Nios input copy and output readback. A DMA
  or a wider/burst path for the input tensor would buy far more end-to-end fps
  than any further clock increase. Even an infinitely fast DLA would only take
  268 fps to ~410 fps, whereas removing the copy bottleneck is worth more than
  doubling the clock again.

## 7. Artifacts

- `build/fpga_ai/phase10_200mhz/` — passing SOF (byte-identical to what was
  programmed), `NIOSV_lab.qsys`, `axc3000_top.qsf`, `NIOSV_lab_iopll_0.ip`,
  `.sta.summary`, `.fit.summary`, final ELF, `main.c`, UART capture.
- `build/fpga_ai/phase9_good_100mhz/` — the recoverable Phase 9 state.
- `reports/fpga_ai_phase10_200mhz_fastcopy_2026-08-21.txt` — final board capture.
- `reports/fpga_ai_phase10_200mhz_uart_2026-08-21.txt` — first 200 MHz capture.
- `fpga/axc3000_mlperf/phase10_compile1_200mhz.log` — compile 1 log.
- Scripts added: `scripts/probe_dla_fmax.tcl`, `scripts/split_dla_clock.tcl`,
  `scripts/phase10_timing.tcl`, `scripts/regen_system.cmd`,
  `scripts/dump_system.tcl`, `scripts/dump_pll.tcl`, `scripts/test_opt_mode.tcl`.

## 8. Process notes for the next phase

- **The `.qsys` cannot be regenerated on its own.** Its cached footprint for
  `fpga_ai` is stale relative to `NIOSV_lab_fpga_ai_*.ip`, so `qsys-generate`
  fails with *"fpga_ai declares port ... which is missing in file"*. This is true
  of the pristine Phase 9 file too — it is not damage. Any qsys work must re-run
  `rebuild_fpga_ai_instance.tcl` first, then apply deltas, then generate.
  `scripts/regen_system.cmd` does exactly this.
- **Do not run `scripts/build_fpga_ai_rtl.ps1` to regenerate the system.** It
  re-runs `dla_compiler`/`dla_create_ip` against the older
  `resnet8_agx3_logits.arch` and would replace the vendor `V1_8x8_AGX3` IP.
- `qsys-script` needs `--search-path=<generated_ip>,<bridgeDir>,$` and
  `--quartus-project`, or components silently fail to resolve.
- `qsys-script` runs an old Tcl dialect: no `eq` operator, use `string equal`.
- `add_connection` only *warns* on failure and `save_system` will then persist a
  broken system. `split_dla_clock.tcl` gates `save_system` behind an explicit
  verification and errors out rather than saving a bad file.
- PowerShell `Tee-Object` writes UTF-16; UART captures need converting to UTF-8
  before text tooling will match them.
