# FPGA AI Suite RTL hardware run

Date: 2026-08-21 (America/Chicago)

## Verdict

The FPGA AI Suite ResNet-8 graph compiles entirely to FPGA RTL, fits the
AXC3000, meets its 100 MHz constraint, programs, accepts every input beat, and
completes repeated jobs. It does **not** yet pass the inference-correctness
gate. A clean evaluation build returns the same ten FP16 words for zero,
all-255, NHWC synthetic, and NCHW synthetic inputs, instead of the CPU-oracle
logits. Internal counters prove accepted input-feeder-to-sequencer handshakes,
but do not validate the payload values, encoding, or tensor ordering.

Evaluation hardware is intended to produce valid results for its first 10,000
inferences. This run starts below that limit and the out-of-inferences status
is clear. Therefore `license=0` does not explain the first-run numerical
failure. No measured throughput is a valid MLPerf or ResNet-8 benchmark result.
The 200 MHz, 300 MHz, and wider-core experiments remain gated on a correct
100 MHz result.

## Model and graph

- MLPerf Tiny quantized reference model SHA-256:
  `3C002613D1B2475EB51DD78DFB85A546C8AE658DEE71CF6ADE43B022FE205415`.
- Adapted no-Softmax OpenVINO XML used for the programmed run SHA-256:
  `C61E8404AB6A1B034C1D43C14838DE28BAF23D724D19203511ADD3398558CF3B`.
- Location-independent reproduction XML SHA-256 (conversion metadata removed):
  `C104ECDC1E0C1777B0071D51E14119BD6B280CE1B21752FA1F84AAB93DCD4A81`.
- Adapted OpenVINO BIN SHA-256:
  `519DE0A6211125C394F92FCACF5E6F4664B1DA6C9D2B0280B1BD913A0077364B`.
- Tracked 4x4 Agilex-3 architecture SHA-256:
  `ED9E2FC95E64D1F4DF5472E899DF23FF5D9D8FF0A59AFCFA71A42DE0C0B4C8DC`.
- External memory is disabled and parameters are held on chip. HyperRAM is
  held inactive at 0 MHz, below the board's 150 MHz maximum.

Softmax is omitted because `argmax(logits) == argmax(softmax(logits))`. This
also saves logic and makes the raw accelerator result directly diagnosable.
For the deterministic firmware image, the OpenVINO CPU oracle produces class
6. Its adapted logits are:

```text
[-18.216473, -13.232721, -2.9215105, -4.6400456, -24.91876,
 -20.794275, 2.2340949, -22.856518, -5.84302, -21.825397]
```

The adapted and reference graphs agree on the class for both zero and
synthetic diagnostic inputs. The maximum adapted/reference logit difference
for the synthetic input is `0.171854019`.

The normalized XML differs from the programmed-run XML only in OpenVINO
conversion metadata. Its BIN is byte-identical, recompilation gives the same
area/performance result, and all 16 hardware on-chip parameter MIFs match the
parameter files used by the programmed IP. Two compiler emulator-only MIFs are
intentionally not copied into hardware IP.

## Compiler and fit

FPGA AI Suite `2026.1.1+b17` reports one FPGA subgraph and no CPU placement.
Its estimator for the 4x4 core is 393.79 fps at a 350 MHz target, or 1.13
fps/MHz, excluding input/output streamers. The estimator's area model reports
26,158 ALMs, 124 M20Ks, and 6 DSPs.

The complete Quartus board design actually fits as:

| Resource | Used | Device | Utilization |
|---|---:|---:|---:|
| ALMs | 11,373 | 34,000 | 33% |
| RAM bits | 2,109,512 | 5,365,760 | 39% |
| RAM blocks | 126 | 262 | 48% |
| DSP blocks | 6 | 276 | 2% |

At the 100 MHz constraint, worst setup/hold slack is `+3.251/+0.011 ns` and
TimeQuest reports 148.17 MHz Fmax. The fitter reports 86% peak long
high-speed interconnect demand in one region. Therefore 200 or 300 MHz is not
a clock-setting-only change; it will require architectural/pipeline/floorplan
work, and a wider compute core may reduce Fmax through congestion.

The programmed SOF SHA-256 is
`459382CBBBB3E8A59421213961DF75DED2F36CBEC0E3944F405D12A450D8423A`.
The Nios V control/streaming ELF SHA-256 is
`2C9DA5763B635724AC5A5D222754061150978EC5168091C274DA61C1A705C894`.

Platform Designer was regenerated before this SOF was built. The synthesis
report contains no stale `_0` AI-IP paths and uses only the current no-suffix
AI-IP descriptor. The architecture hash and all 16 hardware parameter MIFs
match the generated IP.

## Captured board output

```text
fpga_ai_resnet8 start csr=0x000a0000 bridge=0x000a1000 clock=100000000
raw_outputs words=16: 5058 5098 506c 50f2 5058 5098 506c 50f2 5058 5098 506c 50f2 0000 0000 0000 0000
class=3 output_beats=2
warmups=5 timed=20 cycles_min=1415703 cycles_max=1416148 cycles_avg=1415819 latency_us_avg=14158
bridge_status=0x00000f01 input_tail=000050e0,59d85000,58e050e0 license=0x00000000 irq=0x00000002 diagnostics=0x00000000
```

The same console capture is preserved independently in
`reports/fpga_ai_rtl_uart_2026-08-19.txt`, SHA-256
`29CBD4AE043AA0026A3C00CA7130D7AD8CFFA7AE56D0CF2084C85F1AFDF9BBEC`.

The transport evidence is good: 25 jobs completed (5 warmups + 20 timed), the
bridge accepted the full input, two output beats ended with TLAST, and the
input-tail readback is exact. The result is still invalid: class 3 differs from
the CPU-oracle class 6, and the repeated half-word pattern is not the expected
logit vector. Descriptor diagnostics are zero (including no
`OUT_OF_INFERENCES` flag), and interrupt-control bit 0 does not report a
latched error. The observed timing is only an evaluation-image transport
measurement.

## Clean regenerated evaluation run

Platform Designer regeneration completed in `15.9391927` seconds for the
system script and `19.256645` seconds for synthesis generation. After removing
the obsolete `_0` IP assignment, the clean Quartus compile completed in
`317.4015293` seconds with zero errors. Programming the resulting SOF took
`4.7872612` seconds with zero errors and zero warnings.

The final counter-enabled target capture is preserved in
`reports/fpga_ai_eval_uart_2026-08-21.txt`, SHA-256
`59A68DB1C2A701E7893541F27F3755DFD13C86D57A692D515CF54E0C9AFAA77F`.
For the first zero-input inference in that capture:

```text
input_beats=512 output_beats=2 completions_delta=1
active_clocks_delta=922578 core_clocks_delta=922553
if9_transactions_delta=888576
```

The ten returned FP16 words remain
`5058 5098 506c 50f2 5058 5098 506c 50f2 5058 5098` for every tested input.
This clean run rules out stale Platform Designer files and proves substantive
internal input-feeder activity, but it does not establish correct inference.

## Evaluation-license semantics

The suite's local `dla_create_ip` checks feature `6AF7_018B`. The installed
license does not contain that production feature, so the generated
`dla_output_streamer.sv` SHA-256 is
`03EBE9F13F78E05EB7E8E1EBB809CD02ADA2C2D3BCED3A5B7D790B59230C0218`,
which exactly matches the installed `dla_output_streamerA.sv` evaluation
variant, not the full licensed streamer.

The generated DMA writer similarly matches the installed inference-limited
`dla_dma_writerA.sv` SHA-256
`CB13FE909ED16C48F7568993A90FA7EDAB8DD21FBD26CDDB92687EC13921F86F`.

That selection is expected for an evaluation build and should permit 10,000
valid inferences. On the clean run, one zero image produced 512 accepted input
beats, two output beats, one completion, 922,553 core-active clocks, and
888,576 accepted input-feeder-to-sequencer transactions. Descriptor diagnostics
were zero, including the out-of-inferences bit. The fixed output therefore
cannot be attributed to an exhausted inference limit. Input encoding or
ordering and the downstream numerical path remain unresolved; the protected IP
was not modified or bypassed.

## Throughput scaling after correctness

The first controlled experiment should widen channel/filter parallelism by
changing `c_vector`, `k_vector`, or lane count while holding model, input,
clock, and firmware constant. Compare actual ALMs, M20Ks, routed Fmax, and
target-timed latency—not just the compiler estimate. Because the current
design has substantial ALM headroom but a local routing hot spot, the preferred
order is:

1. Evaluation 4x4 correctness at 100 MHz.
2. One moderate wider-core build at 100 MHz, retaining routing headroom.
3. 200 MHz timing closure and board run.
4. 300 MHz only if 200 MHz closes and remains correct.

The FPGA AI Suite architecture optimizer did not produce a valid constrained
architecture for this quantized graph: it explored a 4x12 candidate but ended
with `Unable to find any architecture that meets the max-resource target`.
That candidate is not a fit or throughput result and must not be treated as
one.

A separately compiled, tracked 8x4 candidate is the preferred first scaling
experiment. It still maps as one FPGA subgraph and the compiler estimates
734.85 fps at 350 MHz (2.10 fps/MHz), excluding streamers, versus 393.79 fps
for 4x4. That is a `1.866x` compute-throughput estimate. With scratchpad depths
trimmed to the compiler's exact requirements, its area estimate is 28,788
ALMs, 127 M20Ks, and 12 DSPs. This is **not** a fit, timing, hardware, or MLPerf
result; it will be generated and placed only after 4x4 correctness.
The candidate architecture SHA-256 is
`72F721C270DD5E07838182F77DCFD07D4D01A52798841B6DDFDC74116CEE5D16`.

## Quartus PATH repeat run (2026-08-21)

The native-Windows environment was explicitly reloaded and verified:
`quartus_sh`, `quartus_pgm`, and `qsys-generate` all resolved from the Quartus
26.1 installation, and `quartus_sh --version` succeeded. Both process- and
user/machine-level license environment configuration were checked without
printing their values. CoreDLA feature `6AF7_018B` remained unavailable.

The existing diagnostic SOF was then programmed again with 0 errors and 0
warnings, the firmware was rebuilt/downloaded, and the target completed 25
jobs. It again returned the repeated `5058 5098 506c 50f2` pattern, class 3,
and `license=0`; the CPU oracle remains class 6. Therefore correcting PATH did
not resolve inference correctness on this system. This is another diagnostic
run, not a benchmark. The independent capture is
`reports/fpga_ai_rtl_uart_2026-08-21.txt`, SHA-256
`51B0CCD2FFDE77EFE98B3A5D4A70638EB164DCD6C018BFC90B6AEEEB1EDF88EA`.
