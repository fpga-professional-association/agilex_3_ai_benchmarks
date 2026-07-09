# hls4ml spatial-dataflow track for CoreDLA-incompatible TinyML models

A concrete realization of issue #24 (M5 Stretch, "Spatial Compiler + custom dataflow decision memo"):
evaluate **hls4ml** as a second inference path for the MLPerf Tiny models the CoreDLA overlay can't
serve well. Fable-authored plan; the de-risking spike is delegated to an opus agent (§6). Per
`AGENTS.md`: no invented numbers; the feasibility of the Agilex-3 backend is treated as an **open
question to be resolved by the spike, not asserted**.

---

## 1. Why hls4ml, and why for *these* models

CoreDLA is a fixed-function **overlay** (`docs/mlperf_tiny_v14_plan.md`, `docs/tinyml_all_models_plan.md`):
models are compiled *onto* one generic engine that streams weights from external DDR. hls4ml is the
**opposite** — a High-Level-Synthesis compiler that emits **custom dataflow hardware per layer**, weights
resident **on-chip**. That difference removes the two walls this project keeps hitting:

1. **Op-placement wall → gone.** The four non-compiling models fail because an op (oversized
   global-average-pool, pre-FC transpose) is outside CoreDLA's fixed module menu, and INT8 graphs can't
   fall back to CPU. hls4ml builds bespoke hardware for whatever supported layers it's given — no menu.
2. **No-DDR wall → gone (the big one for the AXC3000).** CoreDLA *requires* a 256-bit external DDR port;
   the AXC3000 has none (the entire PH3 effort + undocumented CSR handshake exists to work around it).
   hls4ml keeps weights on-chip, so a model that fits needs **no external memory** — it could run on the
   AXC3000 **without finishing PH3**. For on-chip-fitting Tiny models this is potentially the *shortest*
   path to the project's north star (MLPerf Tiny measured on the board).
3. **Sub-8-bit → usable.** hls4ml supports arbitrary `ap_fixed<>` widths (via QKeras), so it can exploit
   the sub-8-bit datapath this project already characterized (L0b soft-MAC density, LV7 Pareto #23) but
   CoreDLA (INT8-locked) cannot.

hls4ml originated for ultra-low-latency inference (CERN/LHC triggers) and already has published MLPerf
Tiny FPGA submissions ([arXiv:2206.11791](https://arxiv.org/abs/2206.11791)) — the target class matches
exactly (sub-100 KB nets, latency-critical).

---

## 2. The crux risk — the Agilex-3 HLS backend (resolve FIRST)

**hls4ml is Xilinx-first.** Its Intel/Altera path is the open question, and this environment already shows
the problem: the `alterafpga/quartus-pro:26.1-agilex3` image contains **no Intel HLS compiler** (`i++`,
`aoc`, `dpcpp`, `icpx` all absent — checked). Intel's classic **HLS Compiler (`i++`)** has been
de-emphasized in favor of the **oneAPI (DPC++/SYCL) FPGA** flow, and **Agilex 3 is brand-new 2025
silicon** whose HLS support is uncertain. So before any model work, the spike must answer, in order:

- **G0 — does hls4ml still ship a working Intel/oneAPI backend?** (Which one — the legacy `Quartus`/`i++`
  backend, a oneAPI backend, or neither in the current hls4ml release?)
- **G1 — is there an HLS→Agilex-3 compiler at all?** (Is `i++` available as a separate Quartus Pro add-on
  / different image, or is oneAPI-for-FPGA the only path, and does *either* target Agilex 3 A3CY100BM16AE7S
  under Quartus Pro 26.1?)
- **G2 — does a trivial hls4ml-generated project actually synthesize + fit** for Agilex 3 (one small dense
  layer, end-to-end through the HLS compiler + Quartus fit, 0 errors)?

**If G0–G2 can't be met, this track is blocked at the toolchain level** — a legitimate, valuable spike
outcome (document it and stop; the CoreDLA compile-fixes remain the path). Do **not** sink effort into
model mapping until G2 passes.

Fallback backends if the Intel path is dead: hls4ml also targets **Catapult HLS** (Siemens) and Xilinx —
neither helps on this Altera board, so a negative G0–G2 likely closes the *FPGA-realized* hls4ml option
on this hardware, leaving only the software/estimate value of §4.

---

## 3. Resource strategy (only relevant once G2 passes)

The constraint on an A3CY100 is **compute unrolling**, not weight storage. Weights fit easily on-chip
(INT8: ad-toycar 267 KB, resnet8 ~78 KB, ds-cnn ~23 KB — all under the 559 KB M20K budget). The fully-
parallel MAC footprint is what blows the 138 DSP / 34K ALM budget, managed by:

- **`reuse_factor`** — serialize MACs to trade latency for area; sweep it to fit the budget (this is the
  hls4ml analogue of L1's PE-array sizing).
- **precision** — `ap_fixed` width per layer; sub-8-bit shrinks both DSP and ALM cost. Use the **L0b
  measured soft-MAC densities** (macs/kALM at INT4/2/1), not theoretical 2×-per-bit, to pick widths.
- **`Strategy: Resource` + `io_type: io_stream`** for the larger conv models to bound resources.

Target: each model closes timing near the ~300 MHz the board's fabric already demonstrates
(`results/l2_m20k_bw_*.json`), fully on-chip, no external memory.

---

## 4. What the spike delivers even if the FPGA backend is blocked

hls4ml's **model→HLS-C++ conversion + bit-accurate fixed-point emulation** runs in pure Python (needs
only `hls4ml` + the ML deps already installed in `.venv`), independent of the HLS *compiler*. So even if
G1/G2 fail, the spike can still: ingest the reference models, produce a fixed-point accuracy number per
precision (feeding the LV7 Pareto with a *second* datapath's accuracy, not just CoreDLA's INT8), and
emit an hls4ml resource **estimate** (labelled ESTIMATE) from the config — quantifying "what a spatial
datapath would cost/buy" for the #24 decision memo without a bitstream.

---

## 5. MLPerf Tiny division framing

- Faithfully implementing the reference model in fixed-point (same architecture, same math to tolerance)
  → **Closed** Division candidate.
- Sub-8-bit, retrained QKeras variants, or architecture changes → **Open** Division.

An hls4ml Closed submission that runs entirely on-chip on the AXC3000 would be a genuinely novel result
(most Tiny FPGA entries are on larger parts); an Open sub-8-bit entry showcases the L0b/LV7 story.

---

## 6. The spike (opus) — scope + go/no-go

Single focused investigation (not a fan-out), bounded to avoid large unprompted installs:

1. **G0/G1 research + probe:** `pip install hls4ml` into `.venv`; enumerate its available backends
   (`hls4ml.backends`); read hls4ml docs/source for the current Intel/oneAPI backend state + Agilex
   support; determine whether an HLS→Agilex-3 compiler exists here or what image/add-on would supply it.
   Report a crisp G0/G1 verdict **before** any heavy step.
2. **Conversion + emulation (backend-independent):** convert one small reference model (start with
   `resnet8-cifar10` or `ds-cnn-kws` from `models/onnx/`) via hls4ml, run its bit-accurate fixed-point
   emulation, report accuracy vs the fp reference at a couple of precisions, and dump the hls4ml resource
   estimate. This works even if G1 fails.
3. **G2 (only if G1 shows a viable compiler):** push one trivial dense layer end-to-end through the HLS
   compiler + a Quartus fit for A3CY100BM16AE7S; report fit/timing/0-errors or the exact blocker.
   Do **not** install a multi-GB oneAPI stack without flagging it back first — report the requirement.

**Go/no-go for the whole track:** G2 pass → open follow-up issues to map all four models (reuse-factor +
precision sweeps) and compare hls4ml-spatial vs CoreDLA-overlay (latency / DSP+ALM+M20K / accuracy /
energy) as the #24 decision memo. G1 fail → record the toolchain blocker, keep only the §4 software value,
and CoreDLA remains the sole FPGA path.

## 7. Comparison methodology (the #24 decision memo output)

For any model realized both ways, tabulate on the AXC3000: **latency** (single-stream p50/p99),
**resources** (DSP / ALM / M20K), **accuracy** (vs reference, per the parity gate #21), **energy**
(µJ/inf, #22), and **effort/flexibility** (recompile time, op coverage). That table — overlay vs spatial,
on real silicon — is the actual deliverable #24 asks for.

## Sources / ties
- issue #24 (umbrella) · `docs/tinyml_all_models_plan.md` (CoreDLA compile path) · `results/reports/ph0_estimator.md`
  (op-placement root cause) · L0b results (`results/l0b_*`, soft-MAC density) · #23 (LV7 Pareto) ·
  hls4ml MLPerf-Tiny codesign [arXiv:2206.11791](https://arxiv.org/abs/2206.11791) · hls4ml docs (fastmachinelearning.org).
