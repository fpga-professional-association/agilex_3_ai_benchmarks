# FPGA AI Suite RTL hardware run

Date: 2026-08-19 (America/Chicago)

## Verdict

The FPGA AI Suite ResNet-8 graph compiles entirely to FPGA RTL, fits the
AXC3000, meets its 100 MHz constraint, programs, accepts every input beat, and
completes repeated jobs. It does **not** yet pass the inference-correctness
gate. The installed license files do not contain the CoreDLA hardware feature
`6AF7_018B`, so `dla_create_ip` selected its inference-limited (`A`) output
streamer and DMA writer. The board returned a repeated diagnostic-looking
pattern rather than the CPU-oracle logits. That pattern is consistent with the
inference-limited image, although the protected RTL does not expose enough
plaintext behavior to prove the exact mechanism. No throughput from this image
is a valid MLPerf or ResNet-8 benchmark result.

The 200 MHz, 300 MHz, and wider-core experiments are intentionally gated on a
correct licensed 100 MHz result.

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
| ALMs | 11,354 | 34,000 | 33% |
| RAM bits | 2,109,512 | 5,365,760 | 39% |
| RAM blocks | 126 | 262 | 48% |
| DSP blocks | 6 | 276 | 2% |

At the 100 MHz constraint, worst setup/hold slack is `+2.673/+0.001 ns` and
TimeQuest reports 136.48 MHz Fmax. The fitter also reports 99% peak long
high-speed interconnect demand in one region. Therefore 200 or 300 MHz is not
a clock-setting-only change; it will require architectural/pipeline/floorplan
work, and a wider compute core may reduce Fmax through congestion.

The programmed SOF SHA-256 is
`2A2D6855BA8A711CC3D900EB06CEAC1C6A1F97425CB73E928349913AF4CCB1B0`.
The Nios V control/streaming ELF SHA-256 is
`D5E08A534B2026A66CF3A79DF103235F49CA6A8653F45D1287AE75F4BC053BF1`.

The checked-in Qsys/QSF were regenerated after this SOF was built, so this
checkpoint does not claim a byte-for-byte source-to-SOF link. The architecture
hash and 16 hardware parameter MIFs match, and the semantic topology is
recorded, but the next licensed run must rebuild the SOF from the committed
tree before benchmarking.

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
latched error. The observed `14.158 ms` average, or about `70.63 inf/s`, is
only an inference-limited-image transport timing.

## License diagnosis

The suite's local `dla_create_ip` checks feature `6AF7_018B`. Sanitized
`lmutil lmstat` checks found that feature in neither of the two already-known
license files, and Quartus points at one of those files. The generated
`dla_output_streamer.sv` SHA-256 is
`03EBE9F13F78E05EB7E8E1EBB809CD02ADA2C2D3BCED3A5B7D790B59230C0218`,
which exactly matches the installed `dla_output_streamerA.sv` evaluation
variant, not the full licensed streamer.

The generated DMA writer similarly matches the installed inference-limited
`dla_dma_writerA.sv` SHA-256
`CB13FE909ED16C48F7568993A90FA7EDAB8DD21FBD26CDDB92687EC13921F86F`.

This must be resolved with a valid Altera FPGA AI Suite/CoreDLA hardware
license. The protected IP must not be modified or bypassed.

## Throughput scaling after correctness

The first controlled experiment should widen channel/filter parallelism by
changing `c_vector`, `k_vector`, or lane count while holding model, input,
clock, and firmware constant. Compare actual ALMs, M20Ks, routed Fmax, and
target-timed latency—not just the compiler estimate. Because the current
design has substantial ALM headroom but a local routing hot spot, the preferred
order is:

1. Licensed 4x4 correctness at 100 MHz.
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
result; it will be generated and placed only after licensed 4x4 correctness.
The candidate architecture SHA-256 is
`72F721C270DD5E07838182F77DCFD07D4D01A52798841B6DDFDC74116CEE5D16`.
