# hls4ml on Agilex 3 (AXC3000) — de-risking SPIKE findings

Spike for issue #62, executing `docs/hls4ml_spatial_track.md` §6 (scope) against §2 (go/no-go gates
G0/G1/G2). Question: can **hls4ml** target the Agilex 3 `A3CY100BM16AE7S` on the Arrow AXC3000 as a
spatial-dataflow alternative to the CoreDLA overlay for TinyML models?

**Bottom line: NO-GO for an FPGA-realized hls4ml datapath on Agilex 3 today — blocked at the
toolchain level (G1 fails).** There is no HLS→Agilex-3 compiler present in this environment, and none
is obtainable without (a) a multi-GB Intel/Altera oneAPI FPGA install that (b) does not target Agilex 3
anyway. The **software value is real and confirmed (GO)**: hls4ml converts a real MLPerf-Tiny model and
its bit-accurate fixed-point emulation is faithful to the float reference (100% top-1 agreement at high
precision), yielding a second-datapath accuracy/precision characterization and an on-chip resource
estimate for the #24 decision memo — with **no bitstream and no FPGA compiler required**.

Date: 2026-07-09 · hls4ml 1.3.0 · isolated venv (did **not** touch the repo `.venv`).

---

## Verdicts at a glance

| Gate | Question | Verdict | Evidence (short) |
|------|----------|---------|------------------|
| **G0** | Does hls4ml still ship a working Intel/Altera backend? | **PASS (with caveat)** | 1.3.0 registers `quartus` **and** `oneapi` backends. `quartus` (i++) is deprecated; `oneapi` (SYCL) is the maintained one. |
| **G1** | Is there an HLS→Agilex-3 compiler at all, here or obtainable? | **FAIL** | oneAPI backend needs `icpx -fintelfpga` (**absent** on host and in the Quartus 26.1 image). The oneAPI FPGA add-on targets Agilex **7/5** + Stratix 10 — **not Agilex 3** — and is **deprecated/removed after oneAPI 2025.0**, which supports only up to **Quartus 24.3**; Agilex 3 needs **Quartus Pro 26.1**. |
| **G2** | Does a trivial hls4ml project synthesize + fit for Agilex 3? | **NOT ATTEMPTED (correctly)** | Blocked by G1. Per plan §6.3, a multi-GB oneAPI install was **not** performed — and it would not target Agilex 3 regardless. |
| **§4** | Software value: convert + emulate a real Tiny model? | **PASS (GO)** | resnet8-cifar10 → hls4ml → g++ csim runs; 100% top-1 agreement vs fp32 at `ap_fixed<32,12>`; resource ESTIMATE produced. |

---

## G0 — hls4ml Intel/Altera backend state  → PASS (caveat)

`pip install hls4ml` → **hls4ml 1.3.0** (latest). Registered backends:

```
['vivado', 'vivadoaccelerator', 'vitis', 'quartus', 'catapult', 'symbolicexpression', 'oneapi', 'libero']
```

Two Intel/Altera-relevant backends exist:

- **`quartus`** — legacy, drives the **discontinued Intel HLS Compiler `i++`**. hls4ml docs mark it
  *deprecated, to be removed*, and steer users to `oneapi`.
- **`oneapi`** — the maintained replacement. Emits **SYCL** kernels and an AOT build (CMake) that
  invokes `icpx -fsycl -fintelfpga -Xshardware -Xstarget=<device>`. Source evidence
  (`backends/oneapi/oneapi_backend.py`, `templates/oneapi/CMakeLists.txt`):
  - `build()` runs `which icpx` and **raises `RuntimeError('Could not find icpx…')`** if absent.
  - Default target is `part='Agilex7'` → `set(FPGA_DEVICE "Agilex7")`; CMake device examples list only
    Stratix 10 / Arria 10 / Agilex 7. **No Agilex 3 anywhere.**

So hls4ml *can* emit Intel/Altera HLS — the wall is the **compiler + device support** below it (G1).

## G1 — Is there an HLS→Agilex-3 compiler?  → FAIL (the crux)

Decisive facts (host probes + vendor docs):

1. **No HLS compiler in this environment.** On the host, `icpx`, `dpcpp`, `i++`, `aoc`, `icx` are all
   **ABSENT**; `/opt/intel/oneapi` does not exist. The `alterafpga/quartus-pro:26.1-agilex3` Docker
   image likewise ships **no** `i++`/`aoc`/`dpcpp`/`icpx` (established previously; Quartus Pro is the
   fitter/synthesis flow only — it contains no HLS front-end). hls4ml's `oneapi.build()` fails at the
   first `which icpx`.
2. **The only maintained SYCL-HLS path (oneAPI FPGA add-on) does not target Agilex 3.** Altera/Intel
   docs enumerate `-Xstarget` device families of **Agilex 7, Agilex 5, Stratix 10** for the oneAPI FPGA
   add-on. **Agilex 3 is not a supported target.**
3. **That add-on is end-of-life and version-incompatible with Agilex 3's Quartus.** Intel **removed
   Altera FPGA support from the oneAPI DPC++/C++ compiler as of the 2025.1 release**; the last release
   with FPGA support (**2025.0**) supports Quartus Prime Pro only **up to 24.3**. **Agilex 3
   (`A3CY100BM16AE7S`) requires Quartus Pro 26.1** (2025 silicon, per project memory / board notes).
   The versions do not overlap.
4. **The successor path is Altera-owned and not yet shown to cover Agilex 3 HLS.** Post-split, Altera
   states it "will continue to provide FPGA support through their dedicated FPGA software development
   tools." As of this spike there is **no public evidence** that an Altera SYCL/HLS front-end for
   **Agilex 3 under Quartus 26.1** has shipped, and no such tool is present here. It would be a
   **separate, multi-GB download** (and likely license-gated), which the plan explicitly says to flag
   rather than install.
5. **Legacy `i++` is dead too.** The classic HLS Compiler is deprecated in favor of oneAPI and is not
   present; hls4ml's `quartus` backend that used it is itself deprecated.

**Conclusion:** No compiler can turn hls4ml's generated HLS into an Agilex-3 IP core in this
environment, and none is obtainable "without a huge unprompted install" — and even the multi-GB install
(oneAPI 2025.0 FPGA add-on) **does not support Agilex 3 or Quartus 26.1**. G1 fails.

## G2 — trivial dense layer end-to-end on Agilex 3  → NOT ATTEMPTED (blocked by G1)

Per plan §6.3, G2 is only run "if G1 shows a viable compiler." It does not. No oneAPI stack was
installed (correct call: it is multi-GB, and it cannot target Agilex 3 / Quartus 26.1). The exact
missing piece is stated under "What would unblock G2" below. Quartus was **not** invoked, because there
is no HLS front-end to feed it an IP core.

---

## §4 software deliverable — conversion + bit-accurate emulation (GO)

This runs entirely in Python + `g++` C-simulation; **no FPGA compiler needed**. Fixed-point arithmetic
(`ap_fixed` rounding/saturation) is numerically **identical across hls4ml HLS backends**, so these
accuracy numbers hold for the oneAPI/Agilex datapath too — only resource/timing (which need synthesis)
are backend-specific.

Model: **resnet8-cifar10** (`models/onnx/resnet8-cifar10.onnx`, 77,708 params, MLPerf-Tiny image
classification). Front-end: hls4ml QONNX path. Backend: `Vitis` (chosen only because its csim compiles
with `g++`; the numerics are backend-agnostic). `io_type=io_stream`, `Strategy=Resource`.

**ONNX-frontend patch needed (documented finding):** hls4ml's QONNX front-end tripped on these
tf2onnx-exported Tiny graphs in two spots — (a) an empty node name crashed `sanitize_layer_name`
(fixed by qonnx `GiveUniqueNodeNames`/`GiveReadableTensorNames`), and (b) the **global-average-pool +
dense tail** is left channels-first (wrapped in a `Transpose`) and qonnx does not register `AveragePool`
as a channels-last op, so hls4ml's pool handler rejects it. The spike patches the pool to channels-last
(valid because a *global* pool's spatial reduction is layout-invariant). Any future model-mapping work
must carry this patch (see `sw/hls4ml/spike_convert_emulate.py::load_and_prepare_onnx`). ds-cnn-kws
additionally carries `DequantizeLinear` nodes and depthwise convs and would need more front-end work.

**Also found:** hls4ml's **io_stream softmax LUT collapses to uniform output** for this config, so
accuracy is (as is standard for hls4ml) evaluated on **pre-softmax logits** — argmax-equivalent, so
top-1 agreement is unaffected; only a naive post-softmax MAE would be misleading.

### Accuracy vs precision (ESTIMATE via bit-accurate emulation)

N=32 seeded inputs (raw uint8 0–255, matching the model's no-rescale preprocessing); fidelity of
fixed-point logits vs the fp32 ONNX-Runtime reference. Integer bits held at 12 (accumulator headroom
for raw-255 inputs); total width swept → fractional bits vary.

| precision (W,I) | frac bits | top-1 agree vs fp32 | logit rel-L2 err | cosine sim |
|-----------------|-----------|---------------------|------------------|------------|
| `ap_fixed<32,12>` | 20 | **1.000** | 1.0e-3 | 1.00000 |
| `ap_fixed<18,12>` | 6  | 0.812 | 6.1e-1 | 0.799 |
| `ap_fixed<16,12>` | 4  | 0.000 | 8.7e-1 | 0.502 |
| `ap_fixed<14,12>` | 2  | 0.031 | 9.9e-1 | 0.680 |

Raw JSON: `sw/hls4ml/spike_resnet8_emulation.json`.

**Reading these numbers honestly:**
- The **1.000 agreement / 1e-3 error at 32-bit proves the conversion + emulation pipeline is faithful**
  to the float model end-to-end. That is the load-bearing result.
- The **steep drop is an artifact of a single *global* uniform precision, not a floor on hls4ml
  accuracy.** With integer bits pinned at 12 (needed only because the *input* is raw 0–255), internal
  activations (max ≈ 5–10) waste ~8 integer bits, starving the fraction. hls4ml's normal workflow —
  **per-layer precision from activation profiling**, and/or **QKeras quantization-aware training** —
  reallocates those bits and is exactly what recovers low-precision accuracy. This spike deliberately
  did *not* do that tuning, so the low-precision rows are a **worst-case lower bound**.
- **Raw-uint8 input forces ≥9 integer bits**, so a literal "8-bit total" `ap_fixed` is not achievable on
  this reference without input rescaling — that is an **MLPerf Open-division / QKeras-retraining**
  exercise, not this spike.

### Resource ESTIMATE (labelled ESTIMATE — no synthesis)

Analytical (MAC counting from parsed layer shapes; exact DSP/ALM/M20K require HLS synthesis, blocked by
G1):

- **Total MACs / inference: 12,501,632** (dominated by the four 3×3 convs at ~2.36M each).
- **Weight params: 77,706.** On-chip weight storage: **~76 KB @ INT8**, ~152 KB @ 16-bit — comfortably
  under the AXC3000 ~559 KB M20K budget (plan §3). **Storage is not the constraint; compute unrolling
  is.** A fully-parallel datapath (12.5M MACs) massively exceeds the ~138 DSP / ~34K ALM budget, so a
  large `reuse_factor` (serialization) would be mandatory — i.e., latency, not fit, is the wall, exactly
  as plan §3 anticipates. Quantifying that trade-off needs synthesis (blocked).

---

## Recommendation

- **FPGA-realized hls4ml on Agilex 3: NO-GO now.** Close the *bitstream* option for this hardware until
  an Altera HLS/SYCL front-end that (1) runs in this environment and (2) targets Agilex 3 under Quartus
  Pro 26.1 actually ships. CoreDLA remains the sole FPGA inference path on the AXC3000; the
  CoreDLA compile-fixes stay the critical path.
- **Keep the hls4ml *software* track: GO.** It already delivers, with zero FPGA tooling, a
  second-datapath accuracy-vs-precision characterization and an on-chip resource estimate for the #24
  overlay-vs-spatial decision memo. Worth a small follow-up issue to (a) add per-layer precision
  profiling / a QKeras-retrained sub-8-bit variant so the low-precision accuracy is representative
  rather than worst-case, and (b) extend the front-end patch to ds-cnn-kws and the CoreDLA-incompatible
  models — producing real "what a spatial datapath would buy" numbers for the memo.

### What would unblock G2 (exact requirement)

A SYCL/HLS compiler that emits an Agilex-3 IP core, specifically **one of**:
1. **Altera's post-split dedicated FPGA HLS/SYCL tool** for Quartus Pro **26.1** with **Agilex 3
   (`A3CY100BM16AE7S`)** as a supported `-Xstarget`/device — if/when it exists and is licensed
   (multi-GB, separate download; not present here). Then set hls4ml `part`/`FPGA_DEVICE` accordingly.
2. (Not viable) Intel oneAPI FPGA add-on ≤ **2025.0** — ruled out: no Agilex 3 support, Quartus ≤ 24.3.
3. (Not viable) Classic Intel HLS `i++` — deprecated, absent, no Agilex 3.

Until (1) is confirmed available, no amount of hls4ml work produces an Agilex-3 bitstream.

---

## Reproduction

```bash
# isolated venv (do NOT use the repo .venv)
python3 -m venv /path/to/venv-hls4ml
/path/to/venv-hls4ml/bin/pip install hls4ml qonnx onnxruntime onnx numpy

# G0: enumerate backends
/path/to/venv-hls4ml/bin/python -c "import hls4ml; from hls4ml.backends import get_available_backends; \
  print(hls4ml.__version__); print(get_available_backends())"

# G1: confirm no HLS compiler present
for t in icpx dpcpp i++ aoc icx; do command -v $t || echo "$t ABSENT"; done

# §4: conversion + bit-accurate emulation + resource estimate
/path/to/venv-hls4ml/bin/python sw/hls4ml/spike_convert_emulate.py \
  --onnx models/onnx/resnet8-cifar10.onnx \
  --widths 32 18 16 14 --int-bits 12 --n-samples 32 \
  --json-out sw/hls4ml/spike_resnet8_emulation.json
```

## Sources
- Installed package source: `hls4ml/backends/oneapi/oneapi_backend.py`, `hls4ml/templates/oneapi/CMakeLists.txt` (hls4ml 1.3.0).
- hls4ml docs — Quartus backend deprecation & oneAPI backend: fastmachinelearning.org/hls4ml (`backend/quartus.html`, `backend/oneapi.html`, `intro/release_notes.html`).
- Intel oneAPI DPC++/C++ FPGA add-on release notes — Altera FPGA support removed as of 2025.1; last FPGA release 2025.0 supports Quartus ≤ 24.3; `-Xstarget` families Agilex 7/5, Stratix 10.
- Altera HLS Compiler page (i++ deprecated → migrate to oneAPI); Altera oneAPI FPGA add-on / Device Selectors for FPGA docs.
- Project memory: Agilex 3 requires Quartus Pro 26.1; `alterafpga/quartus-pro:26.1-agilex3` image has no i++/aoc/dpcpp/icpx; AXC3000 resource budget (plan §3).
