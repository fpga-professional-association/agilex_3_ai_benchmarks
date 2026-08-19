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
- Accelerator implementation: the no-Softmax quantized graph maps wholly to
  FPGA, integrates, fits, programs, streams, and completes jobs. It fails the
  correctness gate because the evaluation streamer returns class 3/repeated
  data instead of CPU-oracle class 6; license CSR is zero. See
  `reports/fpga_ai_rtl_run.md`. The measured evaluation-image timing is not a
  benchmark result, and 200/300 MHz or wider-core trials remain gated.

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
  200/300 MHz work, or 8x4 hardware work until licensed 4x4 correctness.

Per-agent/model token counts remain unavailable from the agent/tool APIs and
are not estimated. Exact elapsed times are included where a Stopwatch metric
was captured.
