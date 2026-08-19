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

Per-agent token counts are unavailable for every agent and must not be
estimated. No exact Stopwatch/token metric was exposed for unavailable entries.

## Gates and blockers

- Build: minimal final Quartus build succeeded in 245.051386 s. Original Arrow
  baseline is blocked by SLL license `AE7C_0062`.
- Parser: initial/options/audit timings are recorded above; parser gate is
  complete for the current source set.
- Integration: Nios V 26.1 BSP/app/CMake optimized link completed. The
  corrected SOPCINFO map is on-chip `0x0..0x80000`, debug `0x80000`, timer
  `0x90000`, sysid `0x90040`, JTAG UART `0x90048`; no hardware programmed.
  Build wall time was `12.6219946` s; target timing remains unavailable.
  Exact ELF/map section sizes are recorded in `reports/resnet8_niosv_memory.txt`.
- Memory/timing: 512 KiB on-chip RAM; 234,228-byte requested static image
  sections plus 488-byte startup/exception sections; 290,060 bytes remaining;
  final worst setup/hold slack 1.985/0.035 ns; HyperRAM 0 MHz inactive.
- Accuracy: blocked because permitted CIFAR-10 data and a permitted TFLite
  runtime oracle are unavailable; no official accuracy claim may be made.
- Pre-program audit: authorized controlled SOF + ELF bring-up after
  385.4739068 s; exact token usage was not exposed.
