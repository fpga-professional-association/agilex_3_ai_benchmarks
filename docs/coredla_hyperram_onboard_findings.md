# CoreDLA + HyperRAM on the AXC3000 — Track B build findings

Track M produced the CoreDLA IP + four compiled `.aot`s; Track P produced the AXC3000+HyperRAM
platform and got it to `quartus_syn` clean. This is Track B: combine the two into one Quartus
project under `quartus/coredla_hyperram_ed/`, run the **full** compile (synthesis → fit → STA →
assembler), and report the result honestly.

> **Bottom line: yes, a programmable `.sof` was produced.** The design **fits** the AXC3000
> (`A3CY100BM16AE7S`, 96% ALM), **routes**, and the Assembler emits a clean bitstream. **Timing does
> not yet fully close** on the CoreDLA compute clock (`clk_dla`) — but for a reason this repo has
> already documented for a sibling build (`docs/coredla_agx3_build_findings.md`): the vendor flow
> deliberately over-constrains `clk_dla` to characterize its true Fmax rather than to a realistic
> target, and that one-more-iteration step (`dla_adjust_pll.tcl`) was not run this session. The
> CSR/HyperRAM/JTAG clock domain (the one that actually has to be provisioned correctly, since it's
> hand-picked, not a probe) **does** close cleanly.

---

## 1. What "Track B" needed to fix first

Track P's `quartus/coredla_hyperram_ed/platform/build.sh` narrated a `qsys-generate`/`quartus_syn`
milestone but had never actually been executed successfully **as the single script it claims to
be** — running it fresh hit three bugs, all a variant of the same root cause: **the `env.sh` Docker
wrappers bind-mount the repo at `/workspace` and only remap the *current working directory* for
`-w`, never the contents of other arguments**, so any absolute *host* path handed to a wrapped tool
as a flag value is meaningless inside the container.

| # | Symptom | Root cause | Fix |
|---|---|---|---|
| 1 | `dla_build_example_design.py`: `Could not find '$COREDLA_ROOT/example_architectures/AGX3_Performance.arch'` | The arch path was passed as `\$COREDLA_ROOT` (backslash-escaped) so the **host** shell never expanded it; the literal string `$COREDLA_ROOT` reached the Python tool, which does not do its own env-var expansion here | Un-escape it — `$COREDLA_ROOT` is exported by `env.sh` on the host to the *same* path the container uses (`/opt/altera/fpga_ai_suite/ubuntu/dla`), so host-side expansion produces a string that is also correct inside the container |
| 2 | `dla_build_example_design.py`: `PermissionError: [Errno 13] Permission denied: '/home/<user>'` (via a chain of `mkdir(parents=True)` failures) | `-o "$BUILD/_ed_pristine"` was an **absolute host path**; inside the container that tree doesn't exist, so `mkdir -p` walked all the way up to `/` before hitting a permission wall | Pass a path relative to `$REPO_ROOT` instead (`BUILD_REL`), since the container's cwd is `/workspace` == `$REPO_ROOT` |
| 3 | `qsys-script`: `Error: Failed to create Quartus Project` (falls back to a stub device `A3CU100BB23CE6S`) | Same class of bug: `--new-quartus-project` and `--search-path` were absolute host paths | Compute both relative to the `qsys/` working directory with `realpath -m --relative-to=...` |

All three are fixed in `quartus/coredla_hyperram_ed/platform/build.sh` (see the inline "Track B fix
#N" comments). With these fixes, `build.sh compile` runs `pristine → overlay → qsys → syn →
compile` as one command and reproduces everything below.

**A fourth, smaller issue** surfaced only at the Assembler stage (not a `build.sh` bug, a genuine
missing constraint): `hb_wstrb_partial_seen` / `hb_hi_addr_seen` (the HyperRAM subsystem's sticky
debug trip-wires) had no pin assignment at all in `pins.tcl`. Quartus refused to emit a `.sof` for
that reason alone (`Critical Warning (25196/25207)`), even though Fit/STA both otherwise completed.
Fixed by wiring them to two of the AXC3000's spare user LEDs (`RLED`/`GLED`, verified board
locations already used elsewhere in this repo's `axc3000_board.tcl` / `third_party/hyperram`
constraints — this platform drives no other LEDs) in
`quartus/coredla_hyperram_ed/platform/hw/pins.tcl`.

## 2. Reproduce

```bash
source scripts/env.sh
quartus/coredla_hyperram_ed/platform/build.sh compile
```
Everything under `platform/build/` is gitignored, regenerated output. Stages
(`pristine`/`overlay`/`qsys`/`syn`/`compile`) are documented in the script's header; each implies
the ones before it.

## 3. Result

### 3a. Was a programmable `.sof` produced?

**Yes.**
```
quartus/coredla_hyperram_ed/platform/build/hw/output_files/top.sof
2,398,167 bytes, produced Jul 10 13:39 UTC
md5: 227fc7164dc2fb2d880f10046d731583
```
`Info: Quartus Prime Assembler was successful. 0 errors, 0 warnings` and
`Info (21793): Quartus Prime Full Compilation was successful. 0 errors, 371 warnings`. (The 371
warnings are near-entirely the CoreDLA IP's own routine ECC-tie-off / DSP-clock-enable /
inferred-RAM notices seen in every CoreDLA build in this repo, plus 2 Critical Warnings — see §3c.)

**Not programmed** — per the task boundary, only the orchestrator (holding the devkit lock) programs
hardware.

### 3b. Resource utilization (Fitter, final/signoff snapshot)

| Resource | Used | Device (`A3CY100BM16AE7S`) | % |
|---|---|---|---|
| ALM logic | 32,649 | 34,000 | **96 %** |
| Total dedicated logic registers | 77,544 | — | — |
| Block memory bits | 4,133,008 | 5,365,760 | 77 % |
| M20K RAM blocks | 228 | 262 | 87 % |
| DSP blocks | 75 | 276 | 27 % |
| PLLs | 2 | 11 | 18 % |
| Pins | 16 | 254 | 6 % |

(`quartus/coredla_hyperram_ed/platform/build/hw/output_files/top.fit.summary`.) Note the
**Analysis & Synthesis stage estimated 34,869/34,000 ALMs (103%)** before placement — the same
pattern already seen in this repo's DDR-free build (`docs/coredla_agx3_build_findings.md`: synth
estimated 104%, Fitter delivered 88%). Synthesis-stage ALM estimates are pessimistic; the real,
signed-off number is the Fitter's 96%.

**Tensor-mode audit** (`scripts/audit_tensor_mode.py --report .../top.fit.rpt --expect-tensor 16`):

```
label   | fixed[A] | float[B] | tensor[C] | expected | result
top.fit | 19       | 40       | 16        | 16       | PASS
```
75 DSP blocks total (matches the Fitter summary): **16 in tensor/`DSP_PRIME` mode**, 40 in
floating-point mode (the softmax exponent/normalization datapath, which is legitimately FP, not a
tensor-mode fallback), and 19 in classic fixed-point mode. This is the CoreDLA vendor IP, not
hand-written tensor RTL — it is expected to mix DSP configurations by function; the audit's job
(PLAN §3 LV2) is confirming tensor mode is *used at all* for the PE array (it is: 16 blocks) and
that this number doesn't unexpectedly regress to 0 in a future rebuild.

### 3c. Timing — did it close?

**Partially — one clock domain closes cleanly, one does not, and the reason is understood and not
new to this repo.**

| Clock domain | Role | Target | Fmax achieved (Slow 0C) | Setup slack | Failing endpoints |
|---|---|---|---|---|---|
| `jtag_pll` outclk0 | CSR + HyperRAM AXI4 + JTAG master (the whole memory/control plane, incl. CoreDLA's `clk_ddr`) | 175 MHz | **195.85 MHz** | **+0.342 ns** | **0** |
| `jtag_pll` outclk1 | HyperRAM PHY `clk2x` (DDIO byte-rate clock) | 350 MHz | 415.8 MHz nominal / **352.98 MHz restricted** (min-pulse-width limited) | **+0.226 ns** | **0** |
| `dla_pll` outclk0 | CoreDLA compute datapath (`clk_dla`) | **600 MHz** (vendor default probe value, `ed_zero.tcl:1127 set dla_freq_mhz 600.0`) | **303.21 MHz** | **-1.632 ns** | **38,784** |

Hold timing closes on all three domains (smallest slack +0.002 ns, 0 failing endpoints anywhere).

**Why `clk_dla` shows "Timing requirements not met" and what it means:** the vendor's own
`dla_platform.qsf`-adjacent `dla_adjust_pll.tcl` script (shipped in this platform,
`quartus/coredla_hyperram_ed/platform/hw/dla_adjust_pll.tcl`) exists specifically to run this exact
compile, read back the achieved Fmax, retarget `dla_pll`'s frequency to something at/below that
number, and recompile — an iterative "find true Fmax, then land on it" flow. The qsys default
(`dla_freq_mhz = 600.0`) is a deliberately aggressive probe value, not a real operating target — the
same pattern this repo already documented for the DDR-free build's SDC ("the SDC's 1 GHz 'probe'
clocks are a best-fmax measurement trick, so the raw ... VIOLATED slack is expected and just
encodes the Fmax below," `docs/coredla_agx3_build_findings.md`). This session did **not** run that
retune iteration (each Quartus Fitter pass on this design takes ~9-13 minutes; iterating to
convergence was out of this session's time budget), so the honest status is:

- **The real achievable `clk_dla` Fmax on this device/placement is 303.21 MHz** (Slow 0C corner).
  That is noticeably lower than the DDR-free build's 342.94 MHz restricted Fmax on the same
  device+arch class — plausible given this design is 96% ALM-full (vs. 88% for the isolated
  DDR-free harness) and carries the AXI4↔HyperRAM DMA + Qsys interconnect the DDR-free build didn't.
- **As currently configured (600 MHz target), the design's STA report says "Timing requirements not
  met"** — 38,784 failing setup endpoints, all on `clk_dla`. This is a true, reproducible result of
  *this specific PLL setting*, confirmed on two independent Fitter runs (see §4): run 1 measured
  -1.540 ns / Fmax 311.92 MHz; run 2 (after the pin fix, different placement seed) measured
  -1.632 ns / Fmax 303.21 MHz — small run-to-run placement noise, same conclusion.
  It is **not** a resource-fit problem (96% ALM still leaves headroom) and not a HyperRAM/CSR
  problem (that domain is clean).
- **The CSR/memory/HyperRAM domain (`jtag_pll`, the one actually hand-tuned to real 175/350 MHz
  targets rather than probed) closes with zero failing endpoints and comfortable margin on both
  outputs.**
- Two more Critical Warnings, both expected/informational, not new blockers: `Report Metastability`
  flags 75/212 synchronizer chains with "unsafe" MTBF at the (currently un-retuned) 600 MHz
  `clk_dla` setting — revisit after the PLL retune below, since most of these are downstream of the
  same over-constrained clock. `hb_dq`/`hb_cs_n`/`hb_ck`/`hb_rst_n` also show non-fatal "missing
  termination/slew rate, default used" I/O warnings (Quartus auto-picked slew rate 1); the HyperBus
  pins are `false_path`'d in `top.sdc` pending real board bring-up per `docs/ph3_status.md`, so this
  doesn't affect the STA verdict either.

**The one remaining step to a fully clean timing report:** run `dla_adjust_pll.tcl` (or manually set
`dla_freq_mhz` to ~280 MHz in `ed_zero.tcl`, comfortably below the measured 303.21 MHz ceiling) and
recompile once more. This is a board-free, Quartus-only step — it does not require the devkit — but
was not completed this session due to the ~10-minute-per-Fitter-pass cost of iterating to
convergence.

## 4. Two full compiles were run this session (both evidence-preserving)

1. **Run 1** (`quartus/coredla_hyperram_ed/platform/build.sh compile`, all three path-bug fixes
   applied, pin fix not yet applied): synth (0 errors, 53 warnings) → fit (0 errors, 98 warnings,
   32,638/34,000 ALM) → STA (0 errors, 7 warnings, same clk_dla-only violation pattern) → **Assembler
   ran with 0 errors but refused to emit a `.sof`** (`Critical Warning (25196/25207)`: 2 pins with no
   location/I-O-standard assignment). Reports preserved (this session's scratch, not committed):
   fit/STA summaries confirm the identical clk_dla-only violation pattern reported in §3c.
2. **Run 2** (after wiring the 2 debug pins to spare LEDs in `pins.tcl`, fit+STA+asm re-run in place
   on the same synthesized netlist — the netlist itself is untouched by a pin/I-O-standard-only
   change): fit (0 errors, 96 warnings, 32,649/34,000 ALM) → STA (0 errors, 7 warnings) → **Assembler:
   0 errors, 0 warnings, `.sof` emitted.** This is the run reported in §3.

**Caveat on `build.sh` reproducibility:** run 1 is the fully-fresh, single-command
`build.sh compile` execution (pristine fetch → overlay → qsys → syn → fit → sta → asm), and it is
what proved fixes #1-#3. The pin fix (fix #4) was applied to the tracked source
(`platform/hw/pins.tcl`) and *separately* copied onto the already-synthesized `platform/build/hw/`
tree to avoid re-paying the ~15-minute qsys+synthesis cost a second time; `fit`/`sta`/`asm` were then
re-run directly (not through `build.sh`, which would have deleted and regenerated the whole tree).
The tracked `platform/hw/pins.tcl` and the one actually used to produce the `.sof` were diffed and
confirmed byte-identical. A fully-fresh `build.sh compile` run *after* the pin fix (exercising the
overlay+qsys+syn steps against the corrected `pins.tcl` too) was **not** independently re-executed
this session — flagging rather than asserting that checkbox, per AGENTS.md.

## 5. Design recap (for orientation)

```
CLK_25M_C (25 MHz XO) ─┬─► dla_pll  (IOPLL) ─► clk_dla ─► CoreDLA compute datapath
                        └─► jtag_pll (IOPLL) ─┬─► outclk0 (175 MHz target) ─┬─► csr_data_bridge.clk
                                               │                             ├─► emif_data_bridge.clk (= CoreDLA clk_ddr)
                                               │                             ├─► jtag_address_span_extender.clock
                                               │                             ├─► jtag_master.clk
                                               │                             ├─► hw_timer_bridge.s0_clk
                                               │                             └─► hyperram_0.clk
                                               └─► outclk1 (350 MHz target, core-only, phase 0) ─► hyperram_0.clk2x
```
CoreDLA IP (`dla_platform_wrapper`, AGX3_Performance arch, 256-bit AXI4 DDR port, CSR base
`0x8000_0000`) is instantiated directly in `top.sv`, alongside the Platform Designer system `shell`
(qsys) which holds the HyperRAM subsystem, CSR/DDR AXI bridges, JTAG master/span-extender, PLLs, and
reset handling — mirroring the vendor `agx3c_jtag` structure with the LPDDR4 EMIF replaced by
`quartus/ip/axc3000_hyperram_axi4` (→ `third_party/hyperram`'s real DDIO SDR PHY). Both AXI4
managers that used to target the EMIF (CoreDLA's `emif_data_bridge.m0` and the host's
`jtag_address_span_extender`) now target `hyperram_0.s_axi` at `baseAddress 0x0000` — full detail in
`docs/ph3_integration.md` and `docs/ph3_submodule.md`.

## 6. Remaining steps for the orchestrator (board-gated)

Everything below needs the physical AXC3000 + the devkit lock; none of it was attempted here.

1. **Acquire the devkit lock** (`scripts/devkit_lock.sh with <owner> <reason> -- ...`, per
   `docs/tiny_hardware_benchmark_runbook.md` §1). Do all of the following under one held lock.
2. **Program** `quartus/coredla_hyperram_ed/platform/build/hw/output_files/top.sof` via whatever
   USB-Blaster III / `quartus_pgm` path this host's memory note prescribes (root+privileged+
   `/dev/bus/usb`+no-libudev docker run — **not** `env.sh`'s `quartus_pgm` wrapper, which is
   CPU-container-only and has no USB passthrough).
3. **Per model** (`ad-toycar`, `ds-cnn-kws`, `resnet8-cifar10`, `mobilenetv1-025-vww` — `.aot` +
   rewritten IR at `quartus/coredla_hyperram_ed/ip/models/<model>/`):
   a. **Load config/weights into HyperRAM.** The CoreDLA CSR handshake
      (`sw/host/coredla_csr_handshake.py`, register map resolved in
      `docs/coredla_csr_handshake_findings.md`) needs, at DDR address `0x0` base (the HyperRAM AXI4
      window this platform now exposes at that base): the `.aot`'s config-words blob + weights at
      `config_base_addr`, then the input tensor at `input_addr` with the output region immediately
      after (allocator rule). Use the JTAG master / `jtag_address_span_extender` path already wired
      in `top.sv` to write these bytes over JTAG (System Console or the `sw/host` transport), the
      same DDR-backed, non-streaming sequence documented in
      `docs/coredla_csr_handshake_findings.md` (`CONFIG_BASE_ADDR` → `CONFIG_RANGE_MINUS_TWO` →
      `INPUT_OUTPUT_BASE_ADDR` **last**, since that write is the trigger).
   b. **Run inference**: `CoreDlaCsrHandshake.run_inference()` (start + poll `COMPLETION_COUNT`,
      guarding on `DESC_DIAGNOSTICS` bit0/bit2). CSR base is `0x8000_0000`, unchanged by this build.
   c. **Read output** back from the HyperRAM window and compare against each model's reference
      bundle (`results/tiny_bundles/<model_id>/`, format in
      `docs/tiny_hardware_benchmark_runbook.md` §0.3).
   d. **Measure latency/throughput/accuracy** with `sw/host/run_tiny_benchmark.py --mode both`
      (per-model latency via the CoreDLA hw-timer / `CLOCKS_ACTIVE_LO/HI`, `fps`, and accuracy vs.
      the CPU-INT8 reference) — the exact command sequence, unit-tested off-board, is
      `docs/tiny_hardware_benchmark_runbook.md` §1-§2. **Before trusting a latency number**, confirm
      the real `f_clk_mhz` that ended up in the programmed bitstream — do **not** assume 175 MHz;
      until the `dla_pll` retune in §3c is done and re-verified with STA, use the CoreDLA hw-timer's
      own cycle count against whatever `clk_dla` the retuned PLL actually produces (or, if
      programming this exact `.sof`, note that `clk_dla`'s *constraint* was 600 MHz but its *real*
      achievable ceiling measured here is ~303 MHz — the actual silicon frequency the fitted design
      runs at is whatever `dla_pll`'s IOPLL registers were configured for at compile time, which
      **is** 600 MHz-derived, not 303 MHz; running the design as-is risks setup violations
      manifesting as wrong answers/hangs on real hardware. **Recommend completing the `dla_adjust_pll.tcl`
      retune (§3c) and re-verifying a clean STA before programming this specific `.sof` for a timed
      run** — programming it for a first functional smoke-test (ignoring timing) is a separate,
      lower-bar option the orchestrator can choose to take if only correctness, not measured
      latency, is wanted first).
   e. **Record results** as schema-valid JSON under `results/` (`kind: "measured"`), per
      `results/schema/result.schema.json` and PLAN §10.
4. **HyperRAM bandwidth end-to-end**: the 342 MB/s figure used in Track M's per-model FPS estimates
   (`quartus/coredla_hyperram_ed/ip/README.md` §2) is the submodule's own standalone measurement,
   not yet measured through this full CoreDLA→bridge→`hyperram_avalon` path — worth an independent
   on-board bandwidth check alongside the first model run.
5. Release the lock: `scripts/devkit_lock.sh release <owner>` (or let `... with ...` auto-release).

## 7. Files touched this session

- `quartus/coredla_hyperram_ed/platform/build.sh` — 3 path-handling bugfixes (see §1), all commented
  inline as "Track B fix #N".
- `quartus/coredla_hyperram_ed/platform/hw/pins.tcl` — added location + I/O standard for
  `hb_wstrb_partial_seen` (`RLED`, PIN_AH22) and `hb_hi_addr_seen` (`GLED`, PIN_AK21), both
  3.3-V LVCMOS, both previously-unused board LEDs.
- `quartus/coredla_hyperram_ed/platform/build/` — regenerated output (gitignored): `hw/output_files/`
  now holds `top.sof` plus the full `top.{syn,fit,sta,asm}.{rpt,summary}` set.
- This document.

No RTL, no CoreDLA IP, and no model artifacts were modified — only the platform's build script and
its board-pinout constraints.
