# Plan: get every MLPerf Tiny model onto the AXC3000 — compile → hardware → benchmark

**Goal.** Take all four *core* MLPerf Tiny v1.4 workloads from "3 of 7 compile" to **all four compiled to
the Agilex-3 CoreDLA IP, run on the physical AXC3000, and benchmarked per MLPerf Tiny rules.** This
document is the Fable-authored plan; implementation of the compile-fix layer is delegated to the
opus workflow (see §6). Per `AGENTS.md`: no invented numbers, estimates labelled as estimates.

Scope note: MLPerf Tiny's four *core* tasks are Keyword Spotting (KWS), Visual Wake Words (VWW),
Tiny Image Classification (IC), and Anomaly Detection (AD) — see `docs/mlperf_tiny_v14_plan.md`.
`tiny-yolov3` is **not** an MLPerf Tiny task (this repo's own extra) and is impossible-as-is
(dynamic-control-flow NMS `Loop`) — issue #51; it is out of scope for this plan.

---

## 0. TL;DR

- The four models don't fail on *size* — they fail on **op placement**. CoreDLA is a fixed-function
  overlay, and an INT8 (FakeQuantize) graph must compile **entirely** to FPGA (`HETERO:FPGA`, no CPU
  fallback), so one unplaceable op kills the whole build. Root cause detail: `results/reports/ph0_estimator.md`.
- **All four core models can be made CoreDLA-compilable while staying MLPerf Tiny *Closed* Division**,
  because every required rewrite is *mathematically equivalent* (bit-for-bit in fp, same INT8 requant).
- The blocker to a *hardware benchmark* is **not** the models — it's the PH3 CoreDLA-on-AXC3000
  integration (`docs/ph3_coredla_nextsteps.md`), which is partly blocked (undocumented CSR handshake).
- Toolchain reality (this environment): `dla_compiler` (FPGA AI Suite) is **not installed** and the
  model-prep ML frameworks are **not installed**, so a *verified* compile can't happen here yet — the
  graph-surgery layer is verifiable now (against the cached ONNX with onnxruntime), the compile and
  the on-board benchmark are documented handoffs. See §5.

---

## 1. Closed vs Open Division grouping (the answer)

MLPerf Tiny **Closed** requires a model *mathematically equivalent* to the reference (strict
apples-to-apples); **Open** allows arbitrary architecture/training changes. Grouping the four core
tasks by the *minimum* division a CoreDLA-compilable fix needs:

| Task | Model (`model_id`) | Blocker(s) | Equivalence-preserving fix | Min. Division |
|---|---|---|---|---|
| AD | `ad-toycar` | none | — (already compiles INT8 → FPGA) | **Closed (done)** |
| KWS | `ds-cnn-kws` | global-AvgPool stride/window (25/5) > pool ceiling (3/4) | AvgPool over an H×W grid ≡ a depthwise conv with constant `1/(H·W)` weights (or a cascade of ≤4-stride pools whose product is the same mean) — **exact** | **Closed** (#48) |
| IC | `resnet8-cifar10` | (A) global-AvgPool 8×8 > ceiling; (B) Transpose-not-before-FC | (A) same pool decomposition; (B) a Transpose feeding an FC is a fixed input-axis permutation → **fold it into the FC weight matrix** (permute columns) — **exact** | **Closed** (#49) |
| VWW | `mobilenetv1-025-vww` | Transpose-not-before-FC | fold the Transpose into the FC weights — **exact** | **Closed** (#50) |

**Conclusion: all four → Closed-achievable.** Open Division is only the *fallback*, needed if (a) an
equivalent rewrite turns out impossible for some op the compiler still rejects after the rewrite, or
(b) INT8 accuracy after the equivalent re-export drifts beyond the MLPerf tolerance — unlikely, since
the rewrites are exact in fp and the INT8 requant path is unchanged, but it must be **measured, not
assumed** (§4, accuracy gate). If Open is ever needed, it means retraining a CoreDLA-friendly variant
(smaller-stride reductions, no pre-FC transpose) — larger effort, separate issues.

> ⚠️ **Verify the true blocker first (do not paper over).** For IC and VWW the committed *prose*
> ("Transpose does not precede an FC layer") and the committed raw stderr (`unplaceable Constant_…`)
> in `results/reports/ph0_sweep_attempts.csv` disagree, and the reconciling `dla_compiler` logs were
> gitignored. The compile-fix work must **regenerate the failure fresh** and read the actual current
> error before committing to a rewrite (issues #49/#50 step 1). The rewrites above are the most
> likely fix given the prose, but the fresh error is authoritative.

---

## 2. The compile-fix layer (Phase A — the opus deliverable)

The equivalence-preserving rewrites are pure **ONNX-graph surgery** — they operate on the already-
exported `models/onnx/<model>.onnx` (all three present) and need only `onnx` + `onnxruntime` + `numpy`,
**not** TensorFlow/CoreDLA. That makes them fully unit-testable *now*, on the real models:

1. **`pool_decompose`** — find a global/oversized `AveragePool`/`GlobalAveragePool` node, replace it
   with an equivalent op the pool unit accepts: a depthwise `Conv` with constant `1/(H·W)` kernels
   over the full window (a true mean), or a cascade of ≤4-window/≤4-stride `AveragePool`s whose product
   equals the original mean. Assert the compiler's pool ceiling (`max_window 3`, `max_stride 4`,
   `models/arch/AGX3_Performance.arch`) is satisfied by every emitted node.
2. **`transpose_fold`** — find a `Transpose` immediately feeding a `Gemm`/`MatMul` (the FC), delete it,
   and permute the FC weight matrix's contracting axis by the transpose perm so outputs are identical.
3. **`coredla_friendly` pass** — compose (1)+(2), applied as an ONNX→ONNX post-pass after the base
   export, gated behind an opt-in flag so the stock (Closed-reference) ONNX is still produced by default.

**Verification available now (Closed-equivalence claim):** for each of the three real ONNX models, run
the original and the rewritten graph through `onnxruntime` on a batch of random inputs and assert the
outputs match to fp tolerance. This *proves the Closed-Division equivalence* — the strongest,
hardest-to-argue part — without any of the blocked toolchain. Wired into a pytest that runs in CI once
`onnxruntime` is a test dep.

**Verification that is a handoff (needs the FPGA AI Suite):** re-quantize the rewritten graph to INT8
via NNCF (`sw/model_prep/quantize_int8.py`), then `dla_compiler --march AGX3_Performance.arch
--fplugin HETERO:FPGA …` and confirm 100 % of nodes place on FPGA with no fallback. This is the actual
"compiles to the FPGA" acceptance criterion; it cannot run here (§5) and is documented step-by-step in
each issue's acceptance criteria.

---

## 3. Per-model compile path (summary; full recipes in issues #48–#50)

| Model | Step 1 (reproduce) | Step 2 (rewrite) | Step 3 (requant+compile — handoff) | Accuracy gate |
|---|---|---|---|---|
| ds-cnn-kws | rerun `dla_compiler` to confirm pool node/ceiling | `pool_decompose` on the final AvgPool | NNCF INT8 → `dla_compiler` HETERO:FPGA 100 % | top-1 within ε of fp32 on full Speech Commands test set |
| resnet8-cifar10 | rerun to get the *real* 2nd blocker (Constant vs Transpose) | `pool_decompose` + `transpose_fold` | same | top-1 within ε on CIFAR-10 10 k test |
| mobilenetv1-025-vww | rerun to get the *real* blocker node | `transpose_fold` | same | top-1 within ε on VWW test set |

`ad-toycar` is already compiling (`results/ph0_ad-toycar-*.json`); it needs no rewrite, only inclusion
in the hardware benchmark run.

---

## 4. Hardware bring-up + benchmark per MLPerf Tiny rules (Phase B)

Getting the models compiled is necessary but **not** sufficient for an MLPerf-Tiny-style number. The
on-device benchmark requires the CoreDLA IP actually running on the AXC3000, which is the PH3 effort.

### 4a. Prerequisite — CoreDLA on the AXC3000 (blocked, tracked separately)

Per `docs/ph3_coredla_nextsteps.md`, running *any* CoreDLA inference on the board still needs: the PD
system regen to source `clk2x` from the IOPLL (pattern now solved by the measured HyperRAM bandwidth
harness), 25 MHz IOPLL reparam + regenerated SDC + board pinout, and — the hard one — the **undocumented
CoreDLA CSR start/done handshake** (`sw/host/smoke_infer.py` is `NotImplementedError`). Issue #18 (M3
integration config (a): DDR-free IP + HyperRAM record store) is the umbrella. Until #18 lands, no model
runs on the board regardless of whether it compiles.

### 4b. The benchmark, per MLPerf Tiny v1.4 Closed rules

Once #18 gives a working single-inference path, the benchmark harness (this repo's `sw/host/` runner +
scoreboard RTL, issues #15–#17, already merged) measures, per model, per Closed-Division rules:

- **Latency** — single-stream, median + p99 per-inference (`metrics.latency_us_p50/p99`), method A
  (no memory noise) then method B (HyperRAM-resident), ≥ the MLPerf minimum inference count. The L4
  overlay fixed-cost fit (#20) explains the small-model floor.
- **Accuracy** — full reference test set on the *device* outputs, cross-checked against the CPU INT8
  reference (`sw/model_prep/eval_int8_cpu.py`) via the parity gate (#21). Closed requires the reference
  pre/post-processing (untimed) — already what `sw/model_prep/` implements.
- **Energy** — µJ/inference via the inline USB-C meter (#22, needs-human + needs-hardware).

Datasets are the MLPerf references (Speech Commands v2, VWW/COCO2014, CIFAR-10, ToyADMOS/ToyCar), packed
into the HyperRAM record store (`sw/packer/`, `docs/record_format.md`). Results land as schema-valid
`results/l5_*.json` (`kind: measured`), one per model per method.

### 4c. The bandwidth reality (label: ESTIMATE)

Even fully working, this path is HyperRAM-bandwidth-bound: the measured **342 MB/s** ceiling
(`results/reports/hyperbus_bw.md`) feeding CoreDLA's 256-bit port. The #6 estimator put `ad-toycar` at
521.6 fps assuming 250 MB/s DDR; re-scaled to the measured 342 MB/s that is ≈ 521.6 × 342/250 ≈ **714
fps** (ESTIMATE, memory-bound-linear region, not measured). The larger classifiers (VWW/IC/KWS) will be
proportionally slower — the on-device numbers are the point of Phase B.

---

## 5. Feasibility / toolchain matrix (honest)

| Capability | Needed for | Present in this env? | Consequence |
|---|---|---|---|
| `onnx` + `onnxruntime` + `numpy` | graph surgery + equivalence tests | installable (small) | **Phase A graph surgery is verifiable now** on the real ONNX |
| TensorFlow / OpenVINO / NNCF | re-export from checkpoint, INT8 requant | not installed (heavy) | requant is a handoff; graph surgery works on the *cached* ONNX without them |
| FPGA AI Suite `dla_compiler` image | verify "compiles 100 % to FPGA" | **not installed** (only `quartus-pro`) | the compile acceptance criterion is a documented handoff; may need the licensed `alterafpga/fpgaaisuite` image |
| AXC3000 board + PH3 CoreDLA integration | run + benchmark on hardware | board yes; **integration no** (#18) | Phase B is blocked on #18, not on the models |

**What this plan delivers now vs. hands off:** the opus workflow (§6) implements + *numerically verifies*
the equivalence-preserving graph surgery against the three real MLPerf Tiny ONNX models (the Closed
claim). The INT8-requant + `dla_compiler` compile is coded and documented but verified only once the AI
Suite is installed. The hardware benchmark is Phase B / #18.

---

## 6. Phased roadmap + issue map

- **Phase A0 — graph surgery (opus, this plan):** `sw/model_prep/graph_ops/` (`pool_decompose`,
  `transpose_fold`, `coredla_friendly`) + onnxruntime equivalence tests on the real ONNX. Ships as a PR.
- **Phase A1 — wire + compile (needs AI Suite):** opt-in `coredla_friendly` in the export/convert
  pipeline for ds-cnn/resnet8/vww; regenerate the fresh failures (issues #48–#50 step 1), apply the
  rewrite, NNCF INT8, `dla_compiler` → confirm 100 % FPGA. Closes #48/#49/#50 compile criteria.
- **Phase A2 — accuracy gate:** INT8-on-CPU accuracy of the rewritten models within ε of the reference
  (proves the rewrite didn't cost accuracy → Closed-legal). Feeds #21.
- **Phase B — hardware (needs #18):** CoreDLA-on-AXC3000 bring-up, then latency/accuracy/energy per
  §4b for all four models. Umbrella #18; energy #22; overlay cost #20.
- **Open-Division fallback (only if A1/A2 fail equivalence):** retrain CoreDLA-friendly variants — new
  issues, not opened pre-emptively.

Deliverable definition of done: all four models show a `results/l5_<model>_methodA.json` (`kind:
measured`) with device latency + accuracy matching the CPU reference within the parity gate — at which
point the repo has a real MLPerf-Tiny-style result set on a $129 board.

## Sources

- `docs/mlperf_tiny_v14_plan.md` (v1.4 facts, Closed/Open rules) · `results/reports/ph0_estimator.md`
  (compile-failure root cause) · `docs/ph3_coredla_nextsteps.md` (hardware bring-up) ·
  `results/reports/hyperbus_bw.md` (measured 342 MB/s) · issues #48–#51 (per-model recipes), #18/#20/#21/#22.
