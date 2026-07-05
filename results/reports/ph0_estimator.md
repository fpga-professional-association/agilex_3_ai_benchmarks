# PH0 estimator vs. PLAN §5 — desk-check results (issue #6)

PLAN §9 PH0: before synthesizing anything, run the FPGA AI Suite **performance estimator**
(`dla_compiler --fanalyze-performance`, invocation documented in `docs/toolchain.md`) per model x
architecture x external-memory-bandwidth, and use it to confirm or correct the PLAN §5 roofline
table. This is a desk-check: every number below is `kind: "estimate"`, never `"measured"`.

**Bottom line up front: only 3 of the 7 PLAN §5 models could be compiled at all** by
`dla_compiler` in INT8 (the project's whole quantization premise) against any of the FPGA AI
Suite's three shipped, unmodified Agilex 3 architecture files. All three that did compile show
their own estimator-vs-PLAN disagreement of several-hundred-x to 3-6x — i.e. **every single
number PH0 could actually check came back wrong by more than 2x**, and in one case by nearly
three orders of magnitude. See "Why 4 models don't compile at all" below before reading this as
"the PLAN numbers are just optimistic" — several of these failures are closer to real, separate
tooling gaps than raw performance shortfalls.

Raw sweep: `results/reports/ph0_sweep_attempts.csv` (84 rows = 7 models x 3 committed arch files x
4 BW points, `ok`/`fail` + reason for every attempt). Result JSONs: `results/ph0_*.json` (12 files,
all `kind: "estimate"`, `level: "PH0"`, schema-valid per `python scripts/validate_results.py`).

## Comparison table (BW = 250 MB/s, PLAN's own "default planning point" ~249 MB/s sustained)

| Model | PLAN §5 FPS | Estimator FPS (AGX3_Performance, 250 MB/s) | Ratio (PLAN / estimator) | >2x? |
|---|---|---|---|---|
| AD autoencoder | ~354,000 | **521.6** | **679x** | yes — interpretation below |
| DS-CNN KWS | ~103,000 | *does not compile* (see below) | n/a | n/a |
| ResNet-8 CIFAR-10 | ~22,000 | *does not compile* (see below) | n/a | n/a |
| MobileNetV1-0.25 VWW | ~9,000 | *does not compile* (see below) | n/a | n/a |
| MobileNetV2-1.0 224² | ~40 | **6.40** | **6.25x** | yes — interpretation below |
| ResNet-18 224² | ~17 | **5.24** | **3.24x** | yes — interpretation below |
| Tiny-YOLOv3 416² | ~21 | *does not compile* (see below) | n/a | n/a |

PLAN FPS column is PLAN §5's literal table (cited, not recomputed). All three estimable models
disagree with PLAN by more than 2x — none confirms the PLAN §5 number as-is.

## Full sweep (all 4 BW points, the 3 models that compile)

`AGX3_Performance.arch` only (the only one of the 3 shipped AGX3 arch files that can compile *any*
INT8 graph — see `models/arch/README.md` finding #1). Source: `results/ph0_<model>-agx3-performance-estimator-<bw>mbps_20260704.json`.

| Model | 200 MB/s | 250 MB/s | 333 MB/s | 400 MB/s | memory-bound at all 4? |
|---|---|---|---|---|---|
| AD autoencoder | 417.2 | 521.6 | 730.2 | 884.3 | yes |
| MobileNetV2-1.0 224² | 5.13 | 6.40 | 8.50 | 10.27 | yes |
| ResNet-18 224² | 4.20 | 5.24 | 7.07 | 8.57 | yes |

FPS scales roughly linearly with assumed BW in every case (`memory_bound: true` in every one of
these 12 JSONs) — i.e. at these BW points none of the three has hit its compute ceiling yet;
estimator FPS is 100% a function of the memory-BW assumption fed in, not the PE array.

## Interpretation of every >2x disagreement

### AD autoencoder — 679x (the single biggest finding of this issue)

PLAN §5 marks AD as **M20K-resident, "MEMORY (input feed)" bound** at ~354k FPS: its 267 KB of
INT8 weights fit entirely in the 559 KB on-chip M20K budget, so PLAN's roofline assumed a
DDR-free-style config where weights load once and only the tiny 640 B input record needs to move
per inference. The estimator instead reports (`results/ph0_ad-toycar-agx3-performance-estimator-250mbps_20260704.json`,
`metrics.ddr_filter_reads_mb: 0.53`, `metrics.memory_bound: true`): AD's ~0.26 MB of INT8 weights
are being **re-read from external memory on every single inference**, not held resident on-chip.
That's because `AGX3_Performance.arch` — the only one of the three shipped AGX3 files that can
compile an INT8 graph at all (see below) — is a *streaming* architecture (`dma`/`stream_buffer`
based), not a DDR-free/MIF-resident one, **regardless of how small the model is**. Feeding that
per-inference weight-reload traffic through the assumed 250 MB/s external-memory link, not a
few-hundred-byte input record, is what produces 522 FPS instead of 354k. This is not "AD is
slower than we thought" so much as **"PLAN §5's DDR-free assumption for sub-400KB models has no
vendor-shipped Agilex 3 architecture to run on in this AI Suite release"** — see
`models/arch/README.md` finding #2 for the full arch-file evidence. Until a genuine DDR-free AGX3
arch exists (PLAN §9 PH3's own escape hatch), treat every "M20K-resident" row in PLAN §5 as
**unconfirmed**, not just AD's.

### MobileNetV2-1.0 224² — 6.25x

PLAN §5's flat traffic model (`weights + input + ~2.5 MB activation spill`) gives
`(3.54 MB + 0.147 MB + 2.5 MB) / 249 MB/s ≈ 40 FPS` — matching PLAN's own number, so PLAN's
internal arithmetic is self-consistent. The estimator's actual per-inference DDR traffic
(`results/ph0_mobilenetv2-1.0-imagenet-agx3-performance-estimator-250mbps_20260704.json`) is
**37.97 MB**, ~6.1x PLAN's ~6.19 MB assumption, from two effects PLAN's flat constant doesn't
capture:

- **Weight re-reads**: `ddr_filter_reads_mb: 12.86` vs 3.45 MB of actual on-disk INT8 weight
  bytes -- a **3.7x re-read factor**. `stream_buffer_depth: 8192` (same in all 3 AGX3 files) is
  too small to cache a whole layer's filters for MobileNetV2's channel counts, so filters get
  re-streamed per spatial tile.
- **Activation spill**: `ddr_feature_reads_mb + ddr_feature_writes_mb = 13.35 + 10.96 = 24.31 MB`
  per inference -- **~10x** PLAN §5's flat 2.5 MB spill constant, not "close to it." MobileNetV2's
  depthwise-heavy structure at 224x224 produces much larger inter-layer activation maps than the
  2.5 MB constant assumes.

### ResNet-18 224² — 3.24x

Same two effects, smaller magnitude:
`ddr_filter_reads_mb: 38.37` vs 11.17 MB on-disk weights (**3.3x re-read**), and
`ddr_feature_reads_mb + ddr_feature_writes_mb = 5.81 + 3.26 = 9.07 MB` spill (**3.6x** the 2.5 MB
constant, vs MobileNetV2's ~10x -- ResNet-18's plain 3x3-conv-heavy structure produces
proportionally less inter-layer spill relative to its much larger weight set than MobileNetV2's
depthwise/pointwise mix does).

**Refined traffic-model constant**: across the two >400 KB models that actually compiled, replace
PLAN §5's flat "~2.5 MB activation spill" with a per-model figure that the estimator reports
directly (2.4-10x the flat constant here); replace the implicit "weights read once" assumption
with an explicit re-read factor (3.3-3.7x here) that this AI Suite release's stream-buffer sizing
imposes on every AGX3_Performance-class architecture. Two data points is not enough to fit a new
universal constant -- this refines PLAN §5's model qualitatively (both terms undercount, by
different factors, model-dependently) rather than replacing "2.5 MB" with a single new number.

## Why 4 models don't compile at all

Every failure below is a real `dla_compiler` exit, not a script bug -- reproduce from
`results/reports/ph0_sweep_attempts.csv` or the full logs the sweep leaves under
`models/compiled/_ph0_scratch/sweep_logs/` (gitignored). Two independent causes:

**(A) The two "Small" AGX3 arch files can't compile *any* INT8 graph.** All 72 `AGX3_Small_NoSoftmax`
/ `AGX3_Small_Softmax` attempts (all 7 models x both files x 4 BW points) fail identically:
*"Quantized graphs with FakeQuantize require Scale enabled and Round Clamp activation in the
architecture"* -- neither file sets `pe_array.enable_scale` or `activation.enable_round_clamp`;
only `AGX3_Performance.arch` sets both. See `models/arch/README.md` finding #1.

**(B) Even on `AGX3_Performance.arch`, 4 of 7 models fail for per-model graph-shape reasons.**
Quantized graphs can only compile through a *single*, non-mixed HETERO plugin (`HETERO:FPGA` or
`HETERO:CPU` -- confirmed: the default mixed `HETERO:FPGA,CPU` is rejected outright for any
FakeQuantize graph, see `docs/toolchain.md`). An op the FPGA subgraph collector can't place
therefore has nowhere to fall back to, and the whole compile fails -- verified directly by
compiling the *same* graphs as fp32 (no FakeQuantize) under the default mixed plugin, where the
same problem op cleanly offloads to CPU instead of failing the build:

| Model | Reason (from `dla_compiler`'s own error / Model Analyzer report) | Root cause |
|---|---|---|
| DS-CNN KWS | `Pool (functional_1/average_pooling2d/AvgPool) width/depth & height strides (5/1, 25) exceeds maximum width/height strides (4, 4)` | The reference MLCommons-Tiny KWS model's final global-average-pool (over the full remaining 25x5 time/freq grid) vastly exceeds the `pool` module's `max_window_height/width: 3`, `max_stride: 4` ceiling -- **identical in all 3 shipped AGX3 files**, so this isn't fixable by picking a different vendor arch |
| ResNet-8 CIFAR-10 | Same pool-stride ceiling (`(8/1, 8)` vs max `(4, 4)`) **plus** `Transpose does not precede an FC layer` on a fused conv/BN/activation chain | Two independent blockers on one model |
| MobileNetV1-0.25 VWW | `Transpose does not precede an FC layer` on a fused depthwise/pointwise-conv chain | A graph-shape placement rule this AI Suite release enforces; AGX3 has no shipped `*_LayoutTransform` arch variant (unlike AGX7/AGX5) that might relax it |
| Tiny-YOLOv3 | Unsupported `image_shape` `Parameter` node, no CPU fallback available | The ONNX Model Zoo `tiny-yolov3-11.onnx` used here (per issue #2's own commit message: "informational only, no detection accuracy in v1 scope") bundles its NMS post-processing as an in-graph ONNX `Loop` with a second `image_shape` input -- dynamic control flow that no static-dataflow accelerator, FPGA or otherwise, can run. This is not fixable by any arch choice; it needs a backbone-only re-export (issue #2/#3 scope) |

None of these four is something PH0 can or should fix: the pool-stride and Transpose-placement
issues would require either a modified arch file (this issue's "vendor examples, unmodified" rule
forbids that for v1) or a different model export (issue #2/#3's remit); the YOLO issue needs a
backbone-only re-export, also issue #2/#3's remit. **PLAN §5's ~103k/~22k/~9.0k/~21 FPS numbers for
DS-CNN, ResNet-8, VWW, and Tiny-YOLOv3 are therefore neither confirmed nor contradicted by this PH0
pass** -- they remain exactly where PLAN left them, pending either an arch-file follow-up issue or
re-exported models.

## A note on where the INT8 IR came from

Issues #2 (model zoo) and #3 (quantization pipeline) are **both still open, unmerged PRs, not
independently verified by this session**, as of this issue. Per this batch's explicit guidance,
that dependency was treated as a judgment call rather than an automatic stop: the performance
estimator's FPS output is a function of the compiled graph's op topology, tensor shapes, and
precision annotations, not of whether NNCF's calibration produced numerically-accurate quantized
weights -- so even if issue #3's accuracy numbers turn out to need revision on review, the FPS
estimates here should not. In practice this dependency's unreviewed state showed up concretely,
not just theoretically: `scripts/estimate.py` independently re-hashes every IR file it compiles
and cross-checks against `models/ir/<model>/quant_manifest.json`'s recorded hash, and for
`ad-toycar` the on-disk IR (in the working tree this session started from) does **not** match that
manifest's recorded hash -- file mtimes show the IR was regenerated after the manifest was last
written. This is flagged (not swallowed) in that result's own `notes` field and
`config.quant_manifest_hash_mismatch: true`; it doesn't change the FPS number's validity for the
reason above, but it's a concrete illustration of why this dependency isn't being treated as
settled. See the parent task's final report for the full reasoning.

## Reproduce

```
source scripts/env.sh   # or prefix docker calls with `sg docker -c "..."` if your shell's docker
                         # group membership is stale (see docs/toolchain.md)
python scripts/estimate.py --model ad-toycar --arch models/arch/AGX3_Performance.arch --membw 250
bash scripts/sweep_estimates.sh   # full 7 x 3 x 4 sweep, ~15-20 min, writes results/ph0_*.json
                                   # + results/reports/ph0_sweep_attempts.csv
python scripts/validate_results.py results/ph0_*.json
python scripts/make_report.py     # refreshes results/reports/summary.md's PH0 section too
```
