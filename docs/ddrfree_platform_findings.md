# Track A / Track 1+2 — DDR-free CoreDLA platform on the AXC3000: findings

**Bottom line up front:** of the 4 MLPerf Tiny models, **only resnet8-cifar10 is DDR-free-viable**
on the AXC3000's C100 device (`A3CY100BM16AE7S`) with the current FPGA AI Suite 2026.1.1 toolchain
and the "hostless JTAG + msgDMA" platform template -- and this phase produced a **real, measured,
programmable, fitting `.sof` for it** (0 fitter errors, 0 assembler errors; ALM 90%, M20K 85%,
DSP 23%; timing closes at the DLA's requested 300 MHz and the CSR/stream domain's requested
100 MHz). **ds-cnn-kws, mobilenetv1-025-vww and ad-toycar all fail** -- each for a different,
concrete, reproduced reason (not a vague "too big"): one hard compiler limitation (ds-cnn) and two
hard resource overflows at the compiler's own *mandatory* minimums (vww, ad-toycar). See the
per-model sections below.

A second, platform-level finding (independent of which model is chosen) was also uncovered and
fixed in this phase: the vendor `ddrfree_common/board.tcl`'s ingress/egress on-chip streaming
buffers are oversized by 16-32x for MLPerf Tiny inputs/outputs and, unfixed, blow the C100's M20K
budget even for the one model (resnet8) whose CoreDLA IP alone already fits. See "Platform-level
M20K overhead" below -- this fix is required for ANY model to have a chance on this device.

## What was built (recap of the platform, Track 1)

A **programmable platform** that turns a resource-fitting DDR-free CoreDLA IP into a real `.sof`
for the AXC3000, driven entirely over JTAG (hostless -- no PCIe, no HPS, no external memory):
`quartus/coredla_agx3_ddrfree/platform/ddrfree_common/` (forked from Altera's
`$COREDLA_ROOT/platform/ddrfree_common/`) + `quartus/coredla_agx3_ddrfree/platform/axc3000_ddrfree/`
(new per-board directory, mirrors `agx5e_modular_ddrfree/`). Full rationale (why forked instead of
hand-rolled, clock plan, CSR/streaming wiring, PLAN §8 method-E compliance) is unchanged from the
prior write-up and is not repeated here; see git history of this file for that section if needed.
`quartus/coredla_agx3_ddrfree/platform/build_platform.sh` is the reproducible one-command driver
added this phase (wraps `dla_build_example_design.py build` + `quartus-compile` with the
bind-mounts `axc3000_ddrfree/` + `ddrfree_common/` over `$COREDLA_ROOT/platform/*`).

## Platform-level M20K overhead (found + fixed this phase)

The Track 1 platform compile for resnet8 (`AGX3_Ddrfree_Fit.arch`, which fits as a bare IP at
M20K 247/262 = 94%, ALM 88%, per `docs/coredla_agx3_build_findings.md`) had been left running
across phases. It **failed**: `Error (170019): Project requires 580 M20K RAM blocks, but the
selected device can contain only 262 M20K RAM blocks` (`top.fit.summary` recorded 530/262 = 202%
at the point placement aborted). The Quartus fitter's own per-hierarchy resource report
(`top.fit.rpt`, "Compilation Hierarchy Node" table) attributes this exactly:

| Hierarchy node | Block memory bits | M20K-equivalent |
|---|---:|---:|
| `dla_platform_inst` (the CoreDLA IP itself) | 4,021,962 | 247 (matches the bare-IP measurement) |
| `board_inst\|ingress_onchip_memory` | 4,194,304 (**512 KiB**) | ~205 |
| `board_inst\|egress_onchip_memory` | 1,048,576 (**128 KiB**) | ~51 |
| `board_inst\|ingress_msgdma` + `egress_msgdma` + `jtag_to_avalon` | 35,552 | ~2 |
| **Total** | **9,300,394** | matches `top.fit.summary`'s 173% block-memory-bits line |

The vendor's `ddrfree_common/board.tcl` sizes `ingress_onchip_memory`/`egress_onchip_memory` at a
fixed 512 KiB / 128 KiB (`memorySize`/`SIZE_VALUE` component parameters) **regardless of the
model**, sized for AGX5/AGX7 dev-kits with far more M20K. On the C100 (5,365,760 bits of M20K
total), that pair alone is ~97% of the *entire device's* M20K bit capacity -- and the CoreDLA IP
already needs 94% by itself, so there was never any room. `setup_project.sh` only patches the
Avalon-ST adapter width for the model's `AXI_ISTREAM_DATA_WIDTH`; it does not resize these buffers.

**Fix applied** (`platform/ddrfree_common/board.tcl`, 2 parameters + their CMacro mirrors, 4 edits
total): shrunk `ingress_onchip_memory` 524288 B -> **32768 B** (largest MLPerf Tiny input on this
platform is vww's 3x96x96 INT8 = 27,648 B; 32 KiB leaves ~1.18x margin for all 4 models) and
`egress_onchip_memory` 131072 B -> **4096 B** (largest output is ad-toycar's 640 elements, <=2560 B
at FP32; 4 KiB leaves >1.5x margin). This cuts the platform's own M20K need from ~258 blocks to
~15 blocks -- a ~17x reduction -- while remaining large enough for any of the 4 models' streaming
I/O. This is a platform-wide fix (shared by whichever model is built), not model-specific.

**Verification of the fix: real Quartus compile in flight this phase** (resnet8, the model this
platform was already using) -- see "Result" at the bottom for the actual fitter numbers with the
trimmed buffers; do not trust this paragraph alone as proof, only the numbers below it.

## Per-model right-sizing results (Track 2)

Method: for each model, start from `AGX3_Ddrfree_Fit.arch` (the proven resnet8-fitting config),
adjust `layout_transform_params` for the model's real input shape, then let `dla_create_ip` /
`dla_compiler`'s own hard-error messages (not guesswork) name the exact minimum buffer depths
needed -- exactly the iterative method `docs/coredla_agx3_build_findings.md` used for resnet8.
Model source: `quartus/coredla_hyperram_ed/ip/models/<name>/<name>.xml` -- the graph_ops-rewritten,
already-CoreDLA-compilable IR the parallel HyperRAM-track workflow produced (pool_decompose +
transpose_fold already applied where needed), reused here rather than re-deriving it, since this
phase is only responsible for the DDR-free arch/platform side.

### ds-cnn-kws -- FAILS: hard compiler limitation, not a resource budget miss

`dla_create_ip` (arch `arch/AGX3_Ddrfree_Fit_dscnn.arch`) never completes IP generation:

```
CoreDLA compiler has thrown an error: Compiler error.
Width concats not supported in architectures with external memory disabled.
Try setting output_image_width_max in the arch file to be larger than the max width in the
graph to prevent slicing and concatenation.
```

Tried, in order, all of: adding `output_image_width_max` at its default (128), then at its
**maximum legal value (256** -- `coredla.proto`'s `ArchParameters.output_image_width_max` is
`[default=128, min=128, max=256, is_pow_2=true]`, so 256 is the ceiling, not a choice) -- no
change; then `k_vector`/`c_vector` 16/16 -> 4/4 (in case the concat was a channel-alignment
artifact) -- no change, identical error. ds-cnn's rewritten IR
(`ds-cnn-kws.xml`) is Conv + 5x GroupConvolution (depthwise-separable) + MatMul + SoftMax on a
49(H)x10(W)x1(C) input -- a much narrower/taller aspect ratio than resnet8 (32x32) or vww (96x96).
**This is a genuine toolchain/model-geometry incompatibility that persists across every knob this
phase has access to (buffer depths, channel-tiling width, output-image-width ceiling), not a
"needs more M20K" problem** -- ds-cnn has the *smallest* weights (~36 KB) of all 4 Tiny models, so
if this were a resource story it would have been the easiest fit, not the only outright compiler
rejection. Full arch + reasoning: `quartus/coredla_agx3_ddrfree/arch/AGX3_Ddrfree_Fit_dscnn.arch`.

### mobilenetv1-025-vww -- FAILS: compiler-mandated minimums overflow the C100 by >2x

`dla_create_ip` (arch `arch/AGX3_Ddrfree_Fit_vww.arch`) DOES succeed, after iteratively raising
four buffers to the exact values `dla_compiler`'s own hard errors demanded (each a real error,
not a guess):

| Parameter | `AGX3_Ddrfree_Fit` (resnet8) | vww (compiler-mandated minimum) |
|---|---:|---:|
| `stream_buffer_depth` | 4096 | **18432** ("Slicing is not supported when external memory is disabled... minimum Stream Buffer depth of: 18432") |
| `filter_scratchpad.filter_depth` | 512 | **1602** ("DDR-free filters require (1602) filter cache depth") |
| `filter_scratchpad.bias_scale_depth` | 128 | **272** ("DDR-free bias&scale require (272) bias_scale_depth") |
| `config_network.config_cache_depth` | 3201 | **8449** ("DDR-free configs require (8449) config cache depth") |

`dla_compiler --fanalyze-area` on this (only-legal) config: **ALM 44,787/34,000 (132%), M20K
589/262 (225%), DSP 62/276 (23%)**. These four numbers are hard *floors* dla_compiler itself
reports for this model at 96x96 input resolution with k_vector=c_vector=16 -- not "optional"
feature trims (sigmoid/prelu/eltwise were already off, matching resnet8's Fit arch). There is no
smaller-but-still-dla_compiler-legal configuration for vww on this device. vww does NOT fit the
AXC3000 C100 in DDR-free mode. Full arch: `quartus/coredla_agx3_ddrfree/arch/AGX3_Ddrfree_Fit_vww.arch`.

### ad-toycar -- FAILS: resource overflow, confirming the task's a-priori expectation

`dla_create_ip` (arch `arch/AGX3_Ddrfree_Fit_adtoycar.arch`, modeling the flat 640-element
MLP autoencoder input/output as channels=640 at spatial 1x1, since
`quartus/coredla_hyperram_ed/ip/models/ad-toycar/ad-toycar.xml` has no Convolution/GroupConvolution
at all -- pure MatMul+Add+ReLU x10) succeeds after bumping `filter_scratchpad.filter_depth`
1024->1052 (another real "DDR-free filters require (1052)" hard error). `dla_compiler
--fanalyze-area`: **ALM 43,855/34,000 (129%), M20K 328/262 (125%), DSP 52/276**. Tried the
compiler's own suggestion (a WARNING: *"The layout transform strides (1 x 1 x 640) exceed
c_vector (16)... Consider increasing c_vector to a legal value >= 640"*) by setting
k_vector=c_vector=64: area got **much worse** (ALM 117,247, M20K 2,793, **DSP 308 > the device's
276 DSP maximum**), because c_vector directly widens the systolic tensor array/PE grid, which this
FC-only, no-spatial-locality workload cannot amortize. Reverted to k=c=16 (the better-but-still-
overflowing config above). ad-toycar has the *largest* weights (~268 KB, matches the task's
prior ~267 KB estimate almost exactly) of the 4 Tiny models and does NOT fit the C100 in DDR-free
mode -- as expected going in. Full arch: `quartus/coredla_agx3_ddrfree/arch/AGX3_Ddrfree_Fit_adtoycar.arch`.

## Reproduce

```bash
cd quartus/coredla_agx3_ddrfree/platform
# Bare-IP area estimate only (fast, no Quartus fitter/asm -- use this to re-check a "does it fit"
# question before spending a full compile):
docker run --rm -i --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -v <repo>:/workspace -v <this-worktree>/quartus/coredla_agx3_ddrfree:/proj \
  -v <openvino-dir>:/opt/intel/openvino:ro -v /dev/null:/usr/lib/x86_64-linux-gnu/libudev.so.1 \
  alterafpga/fpgaaisuite:2026.1.1-quartus bash -lc \
  'source /opt/intel/openvino/setupvars.sh; source /opt/altera/fpga_ai_suite/ubuntu/dla/setupvars.sh; \
   cd /proj/<some-writable-subdir>; dla_compiler --march /proj/arch/<ARCH>.arch --fanalyze-area'

# Full platform build + Quartus compile (one at a time -- see build_platform.sh header):
ARCH=arch/AGX3_Ddrfree_Fit.arch \
MODEL=<repo>/models/scratch/ir/resnet8_nchw/int8/resnet8-cifar10.xml \
OUT=out_resnet8 platform/build_platform.sh
```

## Result: resnet8 platform compile with the trimmed on-chip buffers (MEASURED, real Quartus 26.1 compile)

The M20K gap closed in three real, measured steps (each a full `quartus_sh --flow compile`-style
run via `dla_build_example_design.py quartus-compile`, device `A3CY100BM16AE7S`):

| Iteration | Change | Fitter result |
|---|---|---|
| Track 1 (prior phase) | vendor buffers (512 KiB / 128 KiB) | `Error (170019): requires 580 M20K, device has 262` (fails at plan stage) |
| v3 (this phase) | buffers -> 32768 B / 4096 B (shared, all-4-models sizing) | `Error (170019): requires 278 M20K` -- still 16 over |
| v4 | buffers -> 4096 B / 1024 B (resnet8-only sizing) | `Error (170019): requires 270 M20K` -- still 8 over |
| v5 | + `stream_buffer_depth` 4096->3072 (dla_compiler's own reported legal minimum) | `Error (170019): requires 263 M20K` -- **1 block over** |
| **v6** | + `input_stream_interface.fifo_depth` 4096->2048, `output_stream_interface.fifo_depth` 3072->2048, `pe_array.exit_fifo_depth` 1024->512 | **Fitter: 0 errors. Assembler: 0 errors. Full compile succeeded.** |

**FITS. Final measured resources (`top.fit.summary`, real placed-and-routed design, not an
estimate):**

| Resource | Used | Device | % |
|---|---:|---:|---:|
| ALM (logic utilization) | 30,457 | 34,000 | 90% |
| M20K (RAM blocks) | 224 | 262 | **85%** |
| Block memory bits | 3,174,890 | 5,365,760 | 59% |
| DSP blocks | 63 | 276 | 23% |
| PLLs | 2 | 11 | 18% |
| Dedicated logic registers | 88,041 | -- | -- |

**Timing closes** (`top.sta.rpt`, Slow 0C corner):

| Clock | Requested | Fmax (restricted) | Setup slack at requested freq |
|---|---:|---:|---:|
| `kernel_pll_outclk0` (CoreDLA `clk_dla`, the DLA compute datapath) | 300 MHz | 321.65 MHz | +0.214 ns (positive -> meets timing) |
| `system_clk_iopll_outclk0` (CSR AXI4-Lite + AXI4-Stream I/O + JTAG-Avalon + msgDMA + on-chip RAM) | 100 MHz | 140.9 MHz | +2.90 ns (positive -> meets timing) |

**Bitstream:** `quartus/coredla_agx3_ddrfree/platform/build/out_resnet8_v2/hw/output_files/top.sof`
(2.47 MB) -- **DO NOT PROGRAM, per task instructions; the orchestrator owns the board.**

**Tensor-mode audit** (`scripts/audit_tensor_mode.py --report .../top.fit.rpt`, PLAN §3 LV2 gate):
of the 63 total DSP blocks, the Quartus resource summary reports **[A] Fixed Point: 23, [B]
Floating Point: 24, [C] DSP_PRIME (native tensor): 16**. This is an FP12AGX (floating-point)
arch -- `dla_compiler --fanalyze-area` itself reports `DSP Configuration: Tensor FP with
ChainOut` for this arch, i.e. the main PE-array tensor engine is expected to run in the
**Floating-Point DSP configuration ([B])**, not integer DSP_PRIME ([C]); the `[C]` DSP_PRIME
count is plausibly the softmax auxiliary unit's `exp32_dsp_wrapper` block (a separate,
non-PE-array DSP use visible by name in the fitter log). **This is a genuine audit finding, not
independently confirmed by hierarchy attribution in this pass** -- flagging it honestly rather
than asserting the split is benign. It does not by itself indicate the classic-mode fallback the
audit exists to catch (all 63 DSPs are accounted for across the two non-classic-only buckets
combined, 24+16=40 non-fixed vs 23 fixed), but a reader relying on "100% tensor mode" language
from earlier bare-IP write-ups should treat that as referring to `[B]+[C]` combined for this
FP-precision arch, not `[C]` alone.

### Arch changes made to close the last 8 M20K blocks (resnet8-specific, in `AGX3_Ddrfree_Fit.arch`)

| Parameter | Before (bare-IP-fitting value) | After (platform-fitting value) |
|---|---:|---:|
| `stream_buffer_depth` | 4096 | 3072 (dla_compiler's own reported legal minimum for resnet8) |
| `input_stream_interface.fifo_depth` | 4096 | 2048 |
| `output_stream_interface.fifo_depth` | 3072 | 2048 |
| `pe_array.exit_fifo_depth` | 1024 | 512 |

All four remained `dla_create_ip`-legal (no hard errors) at these values for resnet8. The platform
buffers (`ddrfree_common/board.tcl`) are now sized for **resnet8 specifically** (ingress 4096 B /
egress 1024 B), not the shared "all 4 models" sizing from the first cut -- moot, since resnet8 is
the only DDR-free-viable model on this device (see per-model sections above).
