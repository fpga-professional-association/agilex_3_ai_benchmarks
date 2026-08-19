# Agilex-3 MLPerf Tiny demo

This repository contains a reproducible Agilex-3 MLPerf Tiny image-
classification path. Sources are pinned by commit; run `powershell -ExecutionPolicy
Bypass -File scripts/fetch_sources.ps1` on Windows to fetch them. The workflow
is native Windows and does not use WSL.

FPGA AI Suite 2026.1.1 is installed at `C:\altera_pro\2026.1.1` with the
official OpenVINO 2025.4 runtime at `C:\altera_pro\openvino_2025.4.0`.
`dla_compiler` reports `2026.1.1+b17`, and an Agilex-3 area-analysis sanity
check passes. Open a configured Command Prompt with:

```bat
call scripts\ai_suite_env.cmd
dla_compiler --version
```

Native Windows RTL simulation is not supported by this release. FPGA AI Suite
IP/example generation must therefore use `--skip-sim-env`; Quartus compilation
and connected-board execution remain native Windows operations. See
[reports/fpga_ai_suite_install.md](reports/fpga_ai_suite_install.md).

Hardware status: the minimal Quartus build and Nios V/m BSP/application link
succeeded. The derivative has HyperRAM deliberately inactive at exactly **0
MHz**, under the **150 MHz** hard cap. Its 512 KiB on-chip memory contains
234,228 bytes of requested image sections; alignment places the runtime end at
241,092 bytes. Stack and heap share the remaining 283,196-byte linker window.
Final Quartus timing met requirements
(worst setup slack 1.985 ns, worst hold slack 0.035 ns); the 100 MHz Nios clock
closed at 124.77 MHz. The final Quartus build took exactly 245.051386 s and
the Nios BSP/application link took 12.6219946 s.

The original Arrow baseline remains blocked by the unlicensed Synaptic Labs
SLL feature `AE7C_0062` (98.6 s baseline attempt).

The audited SOF and ELF were programmed and run on the connected AXC3000. This
is a scalar Nios V/m smoke/performance baseline, not the requested FPGA AI
Suite accelerator endpoint. The synthetic self-test passed with class 6 and
checksum `0x867c28f5`. After 5 warmups, 20 timed scalar inferences took
70,811,326,870 ticks at 100 MHz (`708.11326870` s): mean latency
`35.405663435` s and throughput `0.02824406897` inferences/s. See
[reports/hardware_run.md](reports/hardware_run.md) for the captured target
output and artifact hashes.

Official accuracy remains blocked: permitted CIFAR-10 evaluation data is not
available, and there is no permitted TFLite runtime oracle. Deterministic host
checksums and synthetic-input smoke tests are regression evidence only, not an
accuracy claim.

The next implementation gate is compiling the pinned ResNet-8 model with FPGA
AI Suite, generating the accelerator IP with `--skip-sim-env`, integrating it
with the Arrow AXC3000 derivative, and running it on hardware. HyperRAM must
remain unused or be clocked at no more than 150 MHz.

See [PROVENANCE.md](PROVENANCE.md) for pinned revisions, hashes, and timing
notes, and [TASK_PROFILE.md](TASK_PROFILE.md) for the execution profile.
