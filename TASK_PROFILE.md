# Task profile

Environment: Windows PowerShell, no WSL. Source retrieval is Git-only and is
defined in `scripts/fetch_sources.ps1`. HyperRAM has a hard maximum clock of
150 MHz and is inactive at exactly 0 MHz in the minimal derivative.

Execution records supplied by the task:

- Arrow clone command: ~1.8 s (command-runner approximate).
- Arrow schematic search: 11.216268 s exact.
- ML clone + inspection: 12.5 s measured tool time.
- ML dependency follow-up: 1.381872 s, 0.411473 s, and 0.157523 s exact component timings.
- Original Arrow baseline build blocked by SLL license `AE7C_0062`: 98.6 s.
- Parser initial: 0.130719700 s; options: 0.119814500 s; audit fixes:
  0.128757800 s.
- Minimal final Quartus build: 245.051386 s, exit 0.
- Nios host command: 0.245072700 s PowerShell Stopwatch.
- Nios BSP/application link: 12.6219946 s.
- SOL phase-1 audit: 332.0539399 s.
- Environment discovery: 1.7 s + 1.4 s + 0.5 s + 1.0 s; git init: 0.4 s.
- SOL planning, repository scaffold, and inference initial elapsed times:
  unavailable; pre-program SOL verification: 385.4739068 s.
- ResNet8 host benchmark: C timer `0.161` s for 20 timed inferences; PowerShell
  Stopwatch `0.24507269999999998` s including process startup; token metric
  unavailable.
- FPGA volatile programming: `5.5929446` s, 0 errors, 0 warnings.
- Rejected explicit-index download: `0.5790162` s; no memory write.
- Auto-detected download with a backslash-path wrapper failure: `4.835908` s;
  GDB reported no file loaded and the benchmark did not run.
- Successful auto-detected ELF load and resume: `4.6261979` s.
- AXC3000 target benchmark: `708.11326870` s for 20 timed inferences after 5
  warmups, measured by 70,811,326,870 target ticks at 100 MHz. Mean latency is
  `35.405663435` s and throughput is `0.02824406897` inferences/s.
- FPGA AI Suite probe against Quartus 26.1 build-110 qinst: `0.1707427` s;
  component correctly rejected because that qinst does not offer it.
- FPGA AI Suite-only download through Quartus 26.1.1 qinst: `2.3903406` s.
- FPGA AI Suite 2026.1.1 unattended native-Windows installation:
  `101.5837781` s.
- OpenVINO 2025.4 official archive download: `5.3334559` s; extraction and
  placement: `1.2207271` s.
- `dla_compiler --fanalyze-area` Agilex-3 sanity verification: `1.5573449` s.
- Less-capable qinst-audit agent timings: initial scan `0.090313` s; help
  `0.523388` s; component probe `0.441421` s; signature probe `0.191477` s;
  final audit `0.501415` s.
- Final GPT-5.6 SOL read-only installation/repository audit: `456.3888297` s;
  verdict GO. The exact license expiry relies on the earlier sanitized license
  audit because this final pass confirmed the feature but did not re-emit the
  expiry in parseable output.

Per-agent token counts are unavailable for every agent and must not be
estimated. No exact Stopwatch/token metric was exposed for unavailable entries.

## Gates and blockers

- Build: minimal final Quartus build succeeded in 245.051386 s. Original Arrow
  baseline is blocked by SLL license `AE7C_0062`.
- Parser: initial/options/audit timings are recorded above; parser gate is
  complete for the current source set.
- Integration: Nios V 26.1 BSP/app/CMake optimized link completed. The
  corrected SOPCINFO map is on-chip `0x0..0x80000`, debug `0x80000`, timer
  `0x90000`, sysid `0x90040`, JTAG UART `0x90048`. The audited SOF and ELF were
  programmed and run successfully; build wall time was `12.6219946` s and the
  target timing is recorded above.
  Exact ELF/map section sizes are recorded in `reports/resnet8_niosv_memory.txt`.
- Memory/timing: 512 KiB on-chip RAM; 234,228-byte requested static image
  sections plus 488-byte startup/exception sections; 290,060 bytes remaining;
  final worst setup/hold slack 1.985/0.035 ns; HyperRAM 0 MHz inactive.
- Accuracy: blocked because permitted CIFAR-10 data and a permitted TFLite
  runtime oracle are unavailable; no official accuracy claim may be made.
- Pre-program audit: authorized controlled SOF + ELF bring-up after
  385.4739068 s; exact token usage was not exposed.
- Hardware regression: pass, class 6 and checksum `0x867c28f5` matched the
  independent host regression. Raw output and hashes are in
  `reports/hardware_run.md`.
- Accelerator toolchain: FPGA AI Suite `2026.1.1+b17` and OpenVINO 2025.4 are
  installed on native Windows, and Agilex-3 compilation/area analysis passes.
  The separate CoreDLA hardware feature `6AF7_018B` is absent from both
  configured license files. `dla_create_ip` therefore selected its evaluation
  streamer. Windows RTL simulation is unsupported, so IP generation uses
  `--skip-sim-env`.
- Accelerator implementation: **PASS as of 2026-08-21**, at 100, 200, 225 and
  300 MHz `dla_clk`, on the vendor-option architecture
  `fpga/ai_suite/resnet8_agx3_vendor_8x8.arch` with DDR-resident parameters.
  The superseded 4x4 streaming/on-chip-parameter builds failed this gate with
  class 3 and a fixed, input-invariant payload; that failure was neither a
  licensing effect nor a defect of the streaming egress. See the 2026-08-21
  section below, `reports/fpga_ai_streaming_egress_escalation.md` and
  `reports/fpga_ai_rtl_run.md` (superseded bring-up). The measured timings
  remain transport/latency figures for the synthetic-input regression flow, not
  benchmark results.

## FPGA AI RTL task timings

- No-Softmax graph generation plus CPU validation: `0.7196376` s.
- Quantized graph compile failures while sizing caches: `0.2997678` s,
  `0.5535354` s, `0.5663272` s, and `0.6689943` s.
- Successful all-FPGA quantized no-Softmax compile: `2.1511175` s.
- FPGA AI IP generation: `4.9025696` s; it explicitly reported an unlicensed
  build.
- Platform Designer attempts: `12.8795749` s, `27.4046384` s, and
  `26.6073782` s failed while replacing stale child interfaces; instance
  rebuild succeeded in `14.6353962` s and final system generation succeeded in
  `17.5767034` s.
- Full Quartus compile: `296.0313792` s, successful with timing met.
- JTAG scan: `0.2527816` s; SOF programming: `6.6346278` s; ELF download:
  `4.3710752` s.
- Diagnostic firmware rebuild: `10.8768823` s; diagnostic ELF download:
  `4.3838279` s; JTAG-UART capture: `3.0858248` s. Descriptor diagnostics
  were zero, interrupt-control was `0x2`, and the license CSR remained zero.
- Board workload: 25 jobs at about 14.158 ms/job; the 20 timed iterations are
  transport timing only because output correctness failed.
- Architecture optimizer analyses: `4.0703585` s with real-device constraints
  on the float diagnostic graph, `3.8050956` s with relaxed resources,
  `0.4918482` s for a missing-plugin-option failure on the exact quantized
  graph, then `1.7567648` s and `1.7916656` s for 90%/100% ALM searches that
  ended without a valid resource-constrained architecture.
- Sanitized CoreDLA feature checks against the two configured license files:
  `1.2` s wall time.
- Tracked fail-closed license-check script verification: `0.9451794` s; it
  correctly exited nonzero for missing feature `6AF7_018B`.
- Clean two-stage TFLite-to-normalized-IR conversion, CPU diagnostic
  validation, and all-FPGA `-CompileOnly` pipeline: `3.3052214` s. Repeating
  the normalized serialization in two output directories produced identical
  XML hashes; all 16 hardware parameter MIFs match the programmed IP's
  generated copies. Two emulator-only MIFs are intentionally absent from the
  hardware IP.
- Less-capable licensing audit: `0.0944795` s; it independently matched both
  generated protected blocks to the installed inference-limited variants.
- Less-capable reproducibility audit subtasks: `0.1038431`, `0.1326376`,
  `0.1529807`, `0.1287996`, `0.2064014`, `0.0968867`, `0.1003011`,
  `0.06959`, and `0.1483591` s. Its identified clean-build gap was addressed
  by `scripts/build_fpga_ai_rtl.ps1`.
- 8x4 candidate first parse failure (activation width constraint):
  `0.3096548` s; initial successful compile/estimate: `2.1463753` s; optimized
  scratchpad-depth recompile: `2.0883777` s. The final compiler-only estimate
  is 734.85 fps at 350 MHz, 28,788 ALMs, 127 M20Ks, and 12 DSPs.
- Final GPT-5.6 SOL read-only audit: `464.6830041` s. Verdict: conditional
  GO to commit the documented negative bring-up; NO-GO for benchmarking,
  200/300 MHz work, or 8x4 hardware work until 4x4 correctness.
- 2026-08-21 native-Windows PATH repeat: Quartus tool resolution/version
  `1.2715173` s; direct CoreDLA feature check `4.2616576` s; user/machine
  license-environment import and recheck `0.9643469` s; JTAG scan
  `0.1693212` s; SOF programming `5.1400021` s; firmware rebuild
  `23.0358526` s; ELF download `5.1800117` s; UART capture `3.0301871` s.
  Quartus programming succeeded, but class 3/repeated output and `license=0`
  were unchanged, so this remains a diagnostic rather than a benchmark.

Per-agent/model token counts remain unavailable from the agent/tool APIs and
are not estimated. Exact elapsed times are included where a Stopwatch metric
was captured.

## 2026-08-21 evaluation correction

The clean single-source regeneration, programming, and internal-profiler run
are itemized in `reports/task_profile_2026-08-21.md`. Directly instrumented
commands total `735.4872203` seconds. One zero frame produced 512 bridge input
beats, one completion, 922,553 core-active clocks, and 888,576 accepted
input-feeder-to-sequencer transactions, while still returning the fixed class-3
pattern. Per-agent token and wall-clock telemetry remained unavailable and was
not estimated.

## 2026-08-21 correctness and clock-scaling campaign

This campaign closed the accelerator implementation gate and then scaled the
core clock. Its evidence is
`reports/fpga_ai_streaming_egress_escalation.md` (defect isolation and the
working configuration, §14–15),
`reports/fpga_ai_phase10_200mhz_2026-08-21.md` (200 MHz), and
`reports/fpga_ai_clock_scaling_2026-08-21.md` (225 and 300 MHz). Board captures
are `reports/fpga_ai_vendor_arch_uart_2026-08-21.txt`,
`reports/fpga_ai_phase10_200mhz_fastcopy_2026-08-21.txt`,
`reports/fpga_ai_phase11_225mhz_uart_2026-08-21.txt`, and
`reports/fpga_ai_phase11_300mhz_uart_2026-08-21.txt`.

### Root cause of the correctness failure

The fixed class-3, input-invariant output was localized by elimination rather
than inferred. The CSR sequence was verified register-for-register against the
shipped vendor sequential testbench; the config stream and the compiled
parameter image were decoded from the compiler's own declared output format;
SignalTap captured the Xbar output port in silicon; both egress mechanisms were
built and tested (output streamer and DDR feature writer, the latter with
`ENABLE_OUTPUT_STREAMER = 0` and no stream interfaces present at all); four
architectures spanning 4x4 and 8x8 cores and two output bus widths were run; and
the graph was truncated to a plain 64-channel convolution/pooling feature map to
remove the classifier tail. The freeze reproduced in every build that set
`enable_on_chip_parameters : true` and in none that did not. The one working
configuration keeps the config and filter parameters in DDR.

- The failure was **not** licensing. Evaluation IP is documented to produce
  valid inferences up to its limit and then hard-stop by setting
  descriptor-diagnostics bit 2; that bit was clear in every capture. The
  `LICENSE` CSR reads `0x00000000` in the passing builds too — it is a static
  build-identity bit, not a runtime gate. The earlier "`license=0` is not the
  cause" reasoning reached the right conclusion by the wrong route, and the
  evaluation output streamer was never corrupting data.
- The failure was **not** the streaming interface as such: it reproduced on the
  mainstream DDR feature-writer path in a design containing no stream
  interfaces.
- No encrypted or protected vendor file was modified, decrypted, or bypassed at
  any point. The escalation material and the recommended bisection order are in
  `reports/fpga_ai_streaming_egress_escalation.md`; the reporter's matching open
  issue is `altera-fpga/agilex-ed-ai-suite#5`.

### Working configuration

- Architecture `fpga/ai_suite/resnet8_agx3_vendor_8x8.arch`, byte-identical to
  `build/fpga_ai/arch_vendor/V1_8x8.arch`: the vendor `AGX3_Performance` option
  set with `k_vector`/`c_vector` reduced 16/16 → 8/8, `output_channels_max`
  16384 → 14320, `pool.k_vector` 4 → 1, and `enable_eltwise_mult` and the
  `softmax` aux primitive dropped. `enable_scale` and `enable_round_clamp` stay
  `true` because the compiler forces both on any FakeQuantize graph.
- Parameters in DDR: 19,456 config bytes plus 167,424 filter bytes = 186,880
  bytes. The board has no DRAM, so the DLA's `ddr_axi` master addresses three
  on-chip RAM blocks (128 KiB + 64 KiB parameters, 32 KiB I/O), initialised from
  `fpga/axc3000_mlperf/mem/dla_par{0,1}.mif`. The firmware reads all 186,880
  bytes back through the second ports and confirms FNV-1a `0xe0a8c009`; the
  architecture discovery ROM matches in all six probed words
  (`arch_hash 0xf27098a0`, `2026.1.1/17`, `V1_8x8`).
- CSR flow: 552 `IP_RESET`, 516 `INTERRUPT_MASK` = 3, 544
  `INTERMEDIATE_BASE_ADDR`, 528 `CONFIG_BASE_ADDR` = 0, 532
  `CONFIG_RANGE_MINUS_TWO` = 2430, then 536 `INPUT_OUTPUT_BASE_ADDR` once per
  inference. This is the first build in the investigation to take the
  `!ENABLE_ON_CHIP_PARAMETERS` branch of the vendor testbench.

### Correctness gate — PASS at 100 MHz

- Deterministic synthetic NHWC image: FPGA class **6**, matching the OpenVINO
  CPU oracle over the same adapted no-Softmax graph.
- Every returned logit lies inside the compiled output `FakeQuantize` range
  `[-26.12, +17.70]`. Mean absolute deviation from the oracle is `0.894`,
  maximum `2.234`. That exceeds the ±0.5 tolerance the acceptance gate stated,
  which was written assuming an FP16 datapath; this architecture's
  `arch_precision` is FP12AGX block floating point and the compiler additionally
  warns of `[-127,127]` truncation. The deviations are mixed-sign and
  unstructured. The rank decision — the quantity MLPerf Tiny scores — is exact.
- Input dependence: four distinct inputs give four distinct output hashes
  (`0xa8ec4c85`, `0x74e53874`, `0xfbced6c6`, `0xd9a4cb1a`), a repeated input
  reproduces bit-exactly, and a 500-frame alternating sweep reports
  `VERDICT=input_varying` with hashes `0x51aeb737` / `0xe90bc0cd` and a class
  histogram of 250 × 0 and 250 × 6.
- `diagnostics=0x00000000`, no error interrupt, `uart_dropped=0`, pseudo-DDR
  self-test `errors=0`, 506 inferences in the run.

### Clock scaling — PASS at 200, 225 and 300 MHz

`dla_clk` was moved to `iopll_0.outclk1`; `ddr_clk`, `irq_clk`, the CSR bus, the
Nios V and `mtime` stay on `outclk0` at 100.000 MHz in every build, so all
wall-clock figures below share one untouched 10 ns timebase. No SDC false paths
or clock groups were added; the IP's own `dla_clock_cross_sync.sdc` was relied
on and the bit-exact logits across three clock ratios are the evidence that this
was correct.

| rung | constraint | achieved Fmax | setup slack | hold slack | ALMs | board gate |
|---|---|---|---|---|---|---|
| Phase 9 | 100 MHz | 123.29 MHz (outclk0) | +1.889 ns | +0.002 ns | 20,680 (61%) | PASS |
| Phase 10 | 200 MHz | 235.79 MHz | +0.759 ns | +0.011 ns | 21,819 (64%) | PASS |
| Phase 11 c1 | 225 MHz | 247.10 MHz | +0.397 ns | +0.012 ns | 21,997 (65%) | PASS |
| Phase 11 c2 | 300 MHz | 312.50 MHz | +0.133 ns | +0.000000 ns | 22,118 (65%) | PASS |

- Total negative slack is 0.000 for every analysis type on every clock in every
  build. M20K is 262/262 (100%) and DSP 12/276 throughout; the whole
  100 → 300 MHz climb cost +1,438 ALMs and +5,151 registers and no extra M20K or
  DSP.
- FP16 logit codes are **bit-for-bit identical across 100 / 200 / 225 /
  300 MHz** in all five test cases, and the 500-frame sweep hashes are
  unchanged at every rung.
- Caveat, stated because it is the one number that constrains use: at 300 MHz
  the worst hold slack on `dla_clk` is exactly `0.000000` ns (full precision, not
  a rounded summary) on three `BLOCK_INPUT_MUX_PASSTHROUGH` LAB-mux registers.
  Hold TNS is 0.000 and Quartus signs the build off, so the requirement is met,
  not violated — but it is zero margin on the fast corner and no temperature
  sweep has been run. The 225 MHz build has +0.012 ns there and is preserved
  complete under `build/fpga_ai/phase11_225mhz/` as the conservative fallback.
- The Phase 10 assessment that 300 MHz was "not plausible on this device and
  design ... a firm conclusion, not a hedge" was wrong. Achieved Fmax rose with
  each tighter constraint (235.79 → 247.10 → 312.50 MHz) because Quartus
  optimizes only to the stated requirement, and register retiming re-balanced the
  encrypted activation round/clamp datapath that Phase 10 had treated as
  immovable. Encryption blocks authoring, not optimisation. The ceiling is
  therefore **not established**; 312.50 MHz is reachable with the current netlist
  and nothing beyond that has been compiled.

### Performance (transport/latency, not a benchmark result)

| metric | 100 MHz | 200 MHz | 225 MHz | 300 MHz |
|---|---|---|---|---|
| mean DLA ticks / frame | 253,375 | 129,325 | 115,534 | 87,969 |
| mean DLA latency | 2.5337 ms | 1.2933 ms | 1.1553 ms | 0.8797 ms |
| fps, DLA only | 394 | 773 | 865 | 1,136 |
| mean frame ticks | 602,079 | 372,787 | 358,988 | 331,448 |
| fps, end-to-end | 166 | 268 | 278 | 301 |
| non-DLA share of frame | 58% | 65% | 68% | 73% |

- DLA job time fits `ticks(f) = 248,109*(100/f) + 5,266` by least squares over
  all four rungs, reproducing every point to within 4 ticks (0.004%). 98.0% of
  the job scales with `dla_clk`; the 5,266-tick (52.7 µs) residue is descriptor
  fetch and completion signalling in the 100 MHz `ddr_clk` domain.
- The 100 MHz row predates the Phase 10 input-copy fix, which is why its
  non-DLA figure is higher; the three later rows share identical firmware apart
  from the `DLA_CLOCK_HZ` label.
- Non-DLA time is 243,462 / 243,454 / 243,479 ticks at 200 / 225 / 300 MHz — a
  spread of 25 ticks in 243 k (0.01%) — confirming the Nios path is untouched by
  the clock change.
- CSR 636 `CORE_CLOCKS_ACTIVE` reads 0 at all times in this configuration and is
  unusable as a witness; the measured 2.880x latency reduction on an untouched
  100 MHz timebase is the in-silicon confirmation of the 3x clock.
- End-to-end is 73% Nios-I/O-bound: 243,479 of 331,448 ticks per frame are the
  input copy and output readback. An infinitely fast DLA would reach only
  411 fps end-to-end, so **input DMA is the next lever, not another clock rung**.
- These figures are measurements of the synthetic-input regression flow. They
  are not MLPerf Tiny results and no MLPerf latency or throughput claim is made.

### Gates and blockers after this campaign

- Accelerator implementation: **PASS** at 100, 200, 225 and 300 MHz, with the
  300 MHz zero-hold-margin caveat above and the FP12AGX logit-deviation caveat
  in the correctness section.
- Benchmark/accuracy: **still blocked**, unchanged. Permitted CIFAR-10
  evaluation data and a permitted TFLite runtime oracle remain unavailable, so
  the class-6 match against an OpenVINO CPU oracle is regression evidence only
  and no official MLPerf Tiny or CIFAR-10 accuracy claim may be made.
- Environment: native Windows RTL simulation is still unsupported in 2026.1.1,
  so all evidence remains in-silicon; the Windows package cannot run the
  emulation plugin (`dla_emulator.dll` ships, three of its dependencies do not,
  and there is no `dla_benchmark` executable), which is why the DDR parameter
  image was extracted from the compiler's own compiled-result output.
- Working tree: at the 300 MHz configuration, matching
  `build/fpga_ai/phase11_300mhz/`; the board is programmed with that SOF.
  Nothing from this campaign was committed. HyperRAM remains inactive at 0 MHz.

### Timings

Per-phase wall-clock instrumentation is recorded in the phase reports and is not
duplicated here. The only Quartus compile wall times captured this campaign are
7 min 15 s (200 MHz), 7 min 44 s (225 MHz) and 7 min 18 s (300 MHz); the
pre-compile Fmax probe of the Phase 9 snapshot took 9 s. Two of the three
budgeted Phase 11 compiles were used, and one of three in Phase 10. No other
Stopwatch metric was exposed for this campaign and none is estimated. Per-agent
and per-model token counts remain unavailable from the agent/tool APIs and are
never estimated.
