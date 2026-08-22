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
analysis. This script targets the superseded 4x4 streaming architecture
`fpga/ai_suite/resnet8_agx3_logits.arch`; running it now would replace the
vendor `V1_8x8_AGX3` IP that the current design uses. Use
`fpga/axc3000_mlperf/scripts/regen_system.cmd` to regenerate the Platform
Designer system instead.

Current-machine caveat: only Python 3.14 is discoverable, NumPy is absent, and
the workspace's older protobuf extension is not Python-3.14 compatible. The
2026-08-21 clean evaluation SOF was therefore regenerated from the
already-created, hash-audited evaluation IP; the complete TFLite-to-IP command
was not rerun in that correction pass. No PyPI package was downloaded.

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

The FPGA AI Suite RTL path is integrated and programmed on the connected
AXC3000. The quantized no-Softmax ResNet-8 graph maps as one FPGA subgraph with
no CPU fallback. The configuration that passes the correctness gate is
`fpga/ai_suite/resnet8_agx3_vendor_8x8.arch` — the vendor `AGX3_Performance`
option set reduced to `k_vector`/`c_vector` 8/8 to fit Agilex 3 — with the
186,880-byte config+filter parameter image resident in DDR rather than on chip,
full DDR input/output, and the descriptor CSR flow taken register-for-register
from the vendor sequential testbench. The board has no DRAM, so "DDR" is three
on-chip RAM blocks on the DLA's `ddr_axi` master; the firmware reads all 186,880
parameter bytes back before the first inference and confirms FNV-1a
`0xe0a8c009` against the host-computed value. Nios V is only the control and
I/O host.

The long-standing fixed class-3 output was not a licensing effect and not a
defect of the streaming interface as such. Exhaustive bring-up — CSR flow
verified against the vendor testbenches, config-stream decode, SignalTap capture
of the Xbar output port, both egress paths (output streamer and DDR feature
writer), four architectures, and a graph truncated to a plain 64-channel feature
map — localized an egress data freeze present in every configuration built with
`enable_on_chip_parameters : true` and absent from the vendor-option
configuration with DDR-resident parameters. The full defect signature, the
bisection order needed to confirm it, and the vendor escalation material are in
[reports/fpga_ai_streaming_egress_escalation.md](reports/fpga_ai_streaming_egress_escalation.md);
the reporter's matching open issue is `altera-fpga/agilex-ed-ai-suite#5`.

Two earlier statements in this file are corrected. Evaluation
(`--unlicensed`) IP is documented to produce valid inferences up to its limit
and then hard-stop by setting descriptor-diagnostics bit 2; the `LICENSE` CSR
reading `0x00000000` is a static build-identity bit, not a runtime gate. The
earlier "`license=0` is not the cause" conclusion was therefore right by
accident, and the evaluation output streamer was never corrupting data. The
earlier 4x4 streaming-architecture figures — 11,373/34,000 ALMs, 126/262 RAM
blocks, 6/276 DSPs, Fmax 148.17 MHz — describe superseded builds, as does the
compiler-only 8x4 candidate estimate.

Correctness at 100 MHz passed on 2026-08-21. The deterministic synthetic NHWC
image classifies as **6**, matching the OpenVINO CPU oracle over the same
adapted graph. Every returned logit lies inside the compiled output
`FakeQuantize` range `[-26.12, +17.70]`; mean absolute deviation from the oracle
is 0.894 and maximum 2.234, which exceeds the ±0.5 tolerance written for an
FP16 datapath but is consistent with this architecture's FP12AGX block
floating point, and the rank decision is exact. Four distinct inputs give four
distinct output hashes, a repeated input reproduces bit-exactly, and a
500-frame alternating sweep reports `VERDICT=input_varying` with a 250/250 class
histogram, `diagnostics=0x00000000` and `uart_dropped=0`. Capture:
`reports/fpga_ai_vendor_arch_uart_2026-08-21.txt`.

`dla_clk` was then split onto its own PLL output, leaving `ddr_clk`, the CSR
bus, the Nios V and `mtime` at 100.000 MHz. Builds closed timing and re-passed
the same board gate at 200 MHz (achieved Fmax 235.79 MHz, setup +0.759 ns),
225 MHz (247.10 MHz, +0.397 ns) and 300 MHz (312.50 MHz, +0.133 ns), with total
negative slack 0.000 on every analysis type in every build. The FP16 logit codes
are bit-for-bit identical across all four rungs in all five test cases, and the
500-frame sweep hashes are unchanged. One caveat must be stated with the
300 MHz build: worst hold slack on `dla_clk` is exactly **0.000000 ns** on three
`BLOCK_INPUT_MUX_PASSTHROUGH` registers. Hold TNS is 0.000 and Quartus signs the
build off, so this is met and not violated, but it is zero margin on the fast
corner and no temperature sweep has been run. The 225 MHz build has +0.012 ns
there and is preserved complete under `build/fpga_ai/phase11_225mhz/` as the
conservative fallback. The 300 MHz build uses 22,118/34,000 ALMs (65%), 49,402
registers, 262/262 M20K (100%), 12/276 DSPs and 1/11 PLLs.

DLA-only latency falls from 2.5337 ms (394 fps) at 100 MHz to 0.8797 ms
(1,136 fps) at 300 MHz. End-to-end throughput went 166 fps at 100 MHz to
268 fps at 200 MHz after a Nios input-copy fix, and to 301 fps at 300 MHz. DLA
job time fits `ticks(f) = 248,109*(100/f) + 5,266` to within 4 ticks (0.004%)
across all four rungs, so 98.0% of the job scales with the core clock and the
52.7 µs residue sits in the 100 MHz `ddr_clk` domain. End-to-end is now 73%
Nios-I/O-bound — 243,479 of 331,448 ticks per frame are the input copy and
output readback — so input DMA, not another clock rung, is the next lever: an
infinitely fast DLA would reach only 411 fps end-to-end. All of these are
transport and latency measurements of the synthetic-input regression flow, not
MLPerf results. See
[reports/fpga_ai_phase10_200mhz_2026-08-21.md](reports/fpga_ai_phase10_200mhz_2026-08-21.md)
and
[reports/fpga_ai_clock_scaling_2026-08-21.md](reports/fpga_ai_clock_scaling_2026-08-21.md).

The earlier scalar Nios V/m implementation remains a software smoke baseline,
not the accelerator endpoint. Its captured run is in
[reports/hardware_run.md](reports/hardware_run.md). The superseded 4x4
evaluation bring-up is in
[reports/fpga_ai_rtl_run.md](reports/fpga_ai_rtl_run.md).

Official accuracy remains blocked: permitted CIFAR-10 evaluation data is not
available, and there is no permitted TFLite runtime oracle. Deterministic host
checksums, the OpenVINO CPU oracle comparison, and synthetic-input sweeps are
regression evidence only, not an MLPerf or CIFAR-10 accuracy claim.

The working tree is at the 300 MHz configuration, matching
`build/fpga_ai/phase11_300mhz/`, and the board is programmed with that SOF.
Nothing from this campaign has been committed. HyperRAM remains inactive at
0 MHz.

See [PROVENANCE.md](PROVENANCE.md) for pinned revisions, hashes, and timing
notes, and [TASK_PROFILE.md](TASK_PROFILE.md) for the execution profile.
