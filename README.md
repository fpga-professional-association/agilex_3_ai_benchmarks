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

Production IP generation fails closed on the separate CoreDLA feature:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/check_coredla_license.ps1
```

The reproducible native-Windows pipeline converts the pinned TFLite model,
validates the no-Softmax IR, compiles it, creates IP, regenerates Platform
Designer, and runs Quartus. Use the explicit evaluation switch for the
inference-limited image (valid for its first 10,000 inferences):

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_fpga_ai_rtl.ps1 -Evaluation
```

Omitting `-Evaluation` selects production mode and requires feature
`6AF7_018B`. Use `-CompileOnly` for license-independent model/compiler
analysis.

Current-machine caveat: only Python 3.14 is discoverable, NumPy is absent, and
the workspace's older protobuf extension is not Python-3.14 compatible. The
clean SOF documented below was therefore regenerated from the already-created,
hash-audited evaluation IP; the complete TFLite-to-IP command was not rerun in
this correction pass. No PyPI package was downloaded.

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

The requested FPGA AI Suite RTL path is now integrated and has been programmed
on the connected AXC3000. The quantized no-Softmax ResNet-8 graph maps as one
FPGA subgraph with no CPU fallback. The full design uses 11,373/34,000 ALMs,
126/262 RAM blocks, and 6/276 DSPs, and meets a 100 MHz constraint with a
reported Fmax of 148.17 MHz. Nios V is only the control/streaming host.

Runtime correctness is unresolved, but it is not explained by `license=0` or
the 10,000-inference limit. A clean evaluation run returned the same ten FP16
words for zero, all-255, NHWC, and NCHW inputs. The first zero frame produced
512 accepted input beats, one completion, 922,553 core-active clocks, and
888,576 input-feeder-to-sequencer transactions; the out-of-inferences bit was
clear. This proves substantial accepted input activity, but it does not verify
the payload values, encoding, or tensor ordering. The measured timing is not a
valid benchmark result. See
[reports/fpga_ai_rtl_run.md](reports/fpga_ai_rtl_run.md).

The earlier scalar Nios V/m implementation remains a software smoke baseline,
not the accelerator endpoint. Its captured run is in
[reports/hardware_run.md](reports/hardware_run.md).

Official accuracy remains blocked: permitted CIFAR-10 evaluation data is not
available, and there is no permitted TFLite runtime oracle. Deterministic host
checksums and synthetic-input smoke tests are regression evidence only, not an
accuracy claim.

The next gate is isolating the numerical failure in the 4x4
evaluation image at 100 MHz. Only after its logits match the CPU oracle will
the tracked 8x4 candidate be placed and run, followed by 200/300 MHz builds.
The compiler-only 8x4 estimate is 734.85 fps at 350 MHz versus 393.79 fps for
4x4 (`1.866x`), with estimated area of 28,788 ALMs, 127 M20Ks, and 12 DSPs.
HyperRAM remains inactive at 0 MHz.

See [PROVENANCE.md](PROVENANCE.md) for pinned revisions, hashes, and timing
notes, and [TASK_PROFILE.md](TASK_PROFILE.md) for the execution profile.
