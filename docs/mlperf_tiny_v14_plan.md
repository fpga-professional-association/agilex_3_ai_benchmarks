# MLPerf Tiny v1.4 adoption plan

Status: **plan only** — no code changed by this document. Every claim below is either cited to a
file already in this repo or flagged as unconfirmed. Per `AGENTS.md`: never invent a measured
number; PLAN.md numbers are not touched here.

MLPerf Tiny v1.4 released 2026-07-07 (record participation: 9 orgs, 25 system configs, Closed +
Open Division). Four **core** reference tasks are architecturally stable since the original
submission round and map 1:1 onto this repo's model registry:

| MLPerf Tiny core task | This repo's `model_id` (`sw/model_prep/models/__init__.py`) |
|---|---|
| Keyword Spotting (DS-CNN, Speech Commands) | `ds-cnn-kws` (`models/dscnn.py`) |
| Visual Wake Words (MobileNetv1-0.25 @96x96) | `mobilenetv1-025-vww` (`models/vww.py`) |
| Tiny Image Classification (ResNet-8, CIFAR-10) | `resnet8-cifar10` (`models/resnet8.py`) |
| Anomaly Detection (FC autoencoder, ToyCar) | `ad-toycar` (`models/ad.py`) |

v1.3 (Sept 2025) added a 5th test — a 1D DS-CNN **streaming** wake-word model — plus a new
open-source test harness. Whether v1.4 changed anything about the four core reference
checkpoints/preprocessing was **not web-confirmable at plan-writing time**; this plan treats that
as an open question with an exact, cheap way to close it (§1), not as a settled fact either way.

---

## 1. Current-vs-v1.4 gap: checkpoint provenance

### What the repo pins today

All four `sw/model_prep/models/{dscnn,resnet8,vww,ad}.py` fetch fp32 TFLite checkpoints straight
from `github.com/mlcommons/tiny` (Apache-2.0, no training in this repo):

| model_id | `_CHECKPOINT_URL` | `_SOURCE_COMMIT` (as written in the `.py` file, today) |
|---|---|---|
| `ds-cnn-kws` | `raw.githubusercontent.com/mlcommons/tiny/master/benchmark/training/keyword_spotting/trained_models/kws_ref_model_float32.tflite` | `"mlcommons/tiny@master"` |
| `resnet8-cifar10` | `raw.githubusercontent.com/mlcommons/tiny/master/benchmark/training/image_classification/trained_models/pretrainedResnet.tflite` | `"mlcommons/tiny@master"` |
| `mobilenetv1-025-vww` | `raw.githubusercontent.com/mlcommons/tiny/master/benchmark/training/visual_wake_words/trained_models/vww_96_float.tflite` | `"mlcommons/tiny@master"` |
| `ad-toycar` | `raw.githubusercontent.com/mlcommons/tiny/master/benchmark/training/anomaly_detection/trained_models/ad01_fp32.tflite` | `"mlcommons/tiny@master"` |

### The gap

All four `_SOURCE_COMMIT` values are the **literal string** `"mlcommons/tiny@master"` — not a
resolved commit SHA. `common.CheckpointManifest` (`sw/model_prep/common.py:87-102`) records this
literal verbatim into every `*.manifest.json`. That means today's manifests cannot answer "which
upstream state produced this checkpoint's sha256?" — `master` is a moving pointer, and both the
URL (`/master/...`) and the recorded provenance field track whatever HEAD happened to be at fetch
time, silently. This is exactly the kind of gap AGENTS.md's "numbers without configs are noise"
principle (PLAN §10) is meant to catch, just one layer up the pipeline (checkpoint provenance,
not a results JSON).

This is *also* the practical blocker to any v1.4-equivalence claim: without a resolved SHA on file,
we cannot state whether the checkpoints already sitting in (gitignored) `models/downloads/` are
the same bytes v1.4's reference tags ship, or something from whenever `master` last moved before
this repo's issue #2 was implemented.

### How to confirm equivalence (desk-doable, no board, no Quartus)

1. `git ls-remote --tags https://github.com/mlcommons/tiny` — resolve the exact v1.4 tag name and
   commit SHA. No auth needed, no clone required.
2. For each of the four URLs above, fetch the **same path** at the resolved SHA instead of
   `/master/` (e.g. `raw.githubusercontent.com/mlcommons/tiny/<SHA>/benchmark/training/keyword_spotting/trained_models/kws_ref_model_float32.tflite`) and `sha256sum` it.
3. Compare against the sha256 already recorded in this repo's `models/downloads/*.manifest.json`
   (if issue #2 has already been run locally) — match means the checkpoint the repo already has
   *is* the v1.4-tagged file; mismatch means upstream changed the reference model and the repo's
   fp32/INT8/accuracy numbers in `results/ph2_*.json` need to be regenerated against the new file.
4. Do the same sha256 check for the two other MLPerf-Tiny-controlled artifacts referenced from
   `mlcommons/tiny`: none of the *datasets* actually live in that repo (Speech Commands v2 via
   `tensorflow_datasets`, DCASE2020 ToyCar via Zenodo, VWW's Silicon Labs COCO2014 mirror, CIFAR-10
   via the `uoft-cs/cifar10` HF mirror — see each model's docstring) so a v1.4 tag bump cannot
   silently change them; only the four TFLite checkpoint paths are in scope for this step.
5. Replace the four `_SOURCE_COMMIT = "mlcommons/tiny@master"` literals with the actual resolved
   SHA (pin to the **commit**, not the tag name — tags can theoretically be force-moved, commits
   cannot), and change `_CHECKPOINT_URL` from `/master/` to `/<SHA>/` for reproducibility. Re-run
   `fetch_models.py` → `export_onnx.py` → `eval_fp32.py` for the four models; commit the refreshed
   manifests. If step 3 found a byte-identical match this is a no-op on the actual weights, purely
   a provenance/reproducibility fix; if it found a mismatch, the refreshed `eval_fp32.py` output
   supersedes the existing `results/ph2_*-fp32_*.json` for that model (never overwrite a
   `kind: measured`/`reference` result silently — land the new one as a new dated file and note
   the checkpoint change in both).
6. **Honesty caveat, stated loudly per the task brief:** step 1-3's actual outcome (does v1.4 tag
   content differ from what's on disk today) was **not established by this plan** — the orchestrator's
   web search at plan-writing time could not confirm v1.4-specific reference-model deltas. Treat
   "the four checkpoints are unchanged in v1.4" as an unverified hypothesis, not a conclusion, until
   step 3 is actually run.

**Marked:** desk (all six steps — no Quartus, no board, no paid tooling).

---

## 2. The v1.3+ streaming DS-CNN test + new open-source harness

v1.3 added a 5th core test (1D DS-CNN streaming wake-word) and MLCommons' own new open-source test
harness (replacing the older closed-source EEMBC runner).

### Streaming DS-CNN as a new model spec

The registry pattern generalizes directly — `sw/model_prep/models/__init__.py` is generic over
`common.ModelSpec`, and every dispatcher (`fetch_models.py`, `export_onnx.py`, `eval_fp32.py`, ...)
is generic over the registry, never hardcoding a model name (per `sw/model_prep/README.md`). Adding
a 5th entry means:

1. **[desk]** Locate the exact streaming-KWS checkpoint path in the `mlcommons/tiny` tree at the
   v1.4 (or v1.3) tag resolved in §1 step 1 — the exact filename/subdirectory was **not confirmed**
   at plan-writing time (not web-indexable yet per the task brief); this is the first concrete
   action, not an assumption.
2. **[desk]** If found, write `sw/model_prep/models/dscnn_streaming.py` following the existing four
   files' shape exactly (module docstring naming the checkpoint source + license + op-for-op
   preprocessing citation, `MODEL_ID`, `EXPECTED_WEIGHT_BYTES`, `fetch_checkpoint`/`fetch_dataset`/
   `export_onnx`/`eval_fp32`/`calibration_samples`/`eval_with_predictor`), register it in
   `models/__init__.py` next to the other four `REGISTRY[...] = common.ModelSpec(...)` entries.
3. **[desk]** Run the existing PH2 pipeline (`fetch_models.py` → `fetch_datasets.py` →
   `export_onnx.py` → `eval_fp32.py`) unmodified against the new model_id — the CLI dispatchers
   need no code change, only the new registry entry.
4. **[needs-quartus]** Run `scripts/estimate.py --model ds-cnn-streaming-kws --arch
   models/arch/AGX3_Performance.arch` (PH0 desk-check, PLAN §9). Expect the same op-placement risk
   class as `ds-cnn-kws`: streaming DS-CNN shares the base DS-CNN convolutional stack, and
   `results/reports/ph0_estimator.md` already shows the *non*-streaming DS-CNN fails to compile on
   every shipped AGX3 arch file (`Pool ... exceeds maximum width/height strides (4, 4)` — a limit
   in every one of the three shipped `.arch` files, not fixable by picking a different one). Do not
   assume the streaming variant will compile; run it and record pass/fail like the existing sweep.
5. **[needs-quartus/needs-hardware, out of this issue's scope]** A *streaming* model is stateful —
   it consumes one frame at a time and carries hidden state across calls, unlike the single-shot
   per-record inference PLAN §6's record-replay harness assumes (`docs/record_format.md`: one
   INT8 tensor + one golden label per fixed-stride record, no cross-record state). Supporting it in
   the on-board harness needs a scoreboard/record-format extension (state buffer in M20K, a new
   CSR or protocol for "feed next frame" vs "read decision") that does not exist today. This is RTL
   design work, not model-prep — flag it as a **follow-up issue**, not something this plan's model
   spec addition solves.

### The new open-source test harness

MLCommons' own harness exists to produce **MLCommons-leaderboard-submittable** results; this
repo's `sw/host/` runner (record-replay + hardware scoreboard, PLAN §6) is independent and serves a
different goal (characterizing *this* silicon, not submitting to MLCommons). Recommend treating
adoption of MLCommons' harness verbatim as **out of scope unless the project's goal explicitly
becomes "submit a compliant result to the MLCommons Tiny leaderboard"** — follow the same
"informational-only, flagged, not silently adopted" precedent this repo already uses for
Tiny-YOLOv3 (`sw/model_prep/README.md`: "Tiny-YOLOv3 is informational-only").

---

## 3. Closed vs Open Division — what this repo can legitimately claim

**Closed Division** requires the model to be mathematically equivalent to the MLPerf reference —
same op graph (up to compiler-legal rewrites that provably preserve output), same weights, same
pre/post-processing. **Open Division** allows architecture/training/dataset/optimizer changes.

This repo's four core-task model specs are already written in a Closed-Division-disciplined way —
worth stating explicitly, not just assumed:

- `models/dscnn.py`: "Preprocessing is TF-op-for-op copied from mlcommons/tiny's
  `get_dataset.py:get_preprocess_audio_func`"
- `models/resnet8.py`: "the upstream pipeline ... feeds raw uint8 pixel values (0..255) straight
  into the model with *no* rescaling layer — reproduced here verbatim"
- `models/vww.py`: reproduces the reference's undocumented train/eval split "deterministically
  (sorted filenames per class, last 10%) ... documented here rather than silently deviating"
- `models/ad.py`: feature extraction "ported from mlcommons/tiny's ... `file_to_vector_array` and
  `01_test.py`"

So the *model-prep* side of this repo is Closed-Division-clean **once §1's checkpoint pin is
confirmed**. The place this project's own work threatens Closed-Division legitimacy is downstream,
at compile time:

`results/reports/ph0_estimator.md` documents four models failing to compile through `dla_compiler`
for **graph-shape / op-placement** reasons — a `Pool` op's window/stride exceeding
`AGX3_Performance.arch`'s `max_window_height/width: 3, max_stride: 4` ceiling (DS-CNN's and
ResNet-8's final global-average-pool), and `Transpose does not precede an FC layer` (ResNet-8,
VWW). Any future fix to make these compile — whether a custom (non-vendor-example) `.arch` file, a
replacement op decomposition, or a graph rewrite — **changes the op graph**. That fix is:

- **Closed-Division-eligible** only if it is *provably* mathematically equivalent: e.g. decomposing
  a global-average-pool that exceeds the arch's stride ceiling into a sequence of smaller,
  arch-legal pooling/reduce ops (or a pool followed by a scalar accumulate) whose composition
  produces **bit-identical** (or, for INT8, identically-rounded) output to the original single-op
  pool, for the exact tensor shapes involved. This must be checked numerically (run both graphs on
  the same input, diff outputs to zero or to the INT8 rounding ULP) before being called equivalent
  — not asserted from the op semantics alone.
- **Open-Division-only** for anything else: a different pooling window/order that changes results
  even slightly, a retrained or re-exported model, or any graph change whose output was not
  numerically verified against the reference. This includes the `Transpose`-placement fixes if they
  require actually reordering tensor layout in a way that changes intermediate numeric values
  (as opposed to a pure metadata/layout annotation that provably doesn't touch values).

**Concrete rule for this repo's `results/*.json`:** any result produced from a graph that differs
from the pinned reference (§1) must record which division it is legitimate for. The schema already
supports this without a schema change — `config` has `"additionalProperties": true`
(`results/schema/result.schema.json`), so a `config.division: "open"|"closed"` key can be added
immediately, plus a `notes` sentence stating *why* ("op decomposition differs from mlcommons/tiny
reference at the final pool; not verified bit-exact" vs "decomposition verified bit-identical to
reference on N=1000 test vectors, see script X"). Never emit a Closed-Division-framed number from a
graph whose equivalence wasn't checked — that is the AGENTS.md "never invent a measured number"
rule applied one level up, to model equivalence rather than to a metric.

---

## 4. What this repo can actually measure toward an MLPerf-Tiny-style number today

### The honest current state

- Of the four actual MLPerf Tiny core tasks, **only `ad-toycar` compiles** through `dla_compiler`
  against any shipped AGX3 arch file (`results/reports/ph0_estimator.md`). `ds-cnn-kws`,
  `resnet8-cifar10`, and `mobilenetv1-025-vww` all fail to compile for the op-placement reasons in
  §3 — none of PLAN §5's ~103k/~22k/~9.0k FPS roofline numbers for those three are confirmed *or*
  contradicted; they remain unverified estimates, and unresolved without either an Open-Division
  arch/graph fix or a provably-equivalent Closed-Division one.
- `ad-toycar`'s own compiled estimate is **memory-bound**, not compute-bound: the estimator reports
  weights re-streamed from external memory on every inference (not resident in M20K despite fitting
  in 559 KB — PLAN's "M20K-resident, DDR-free" assumption for sub-400KB models has **no vendor AGX3
  arch to actually run on** in this AI Suite release; see `models/arch/README.md` finding #2). At
  the assumed 250 MB/s external-memory input, the estimator gives **521.6 fps** — a 679x
  disagreement with PLAN §5's 354k fps roofline, entirely explained by that DDR-free-arch gap.
- The repo's actual **measured** memory ceiling is higher than the 250 MB/s the existing `ad-toycar`
  estimate assumed: HyperRAM sustained bandwidth is **342.4 MB/s write / 337.3 MB/s read, measured
  on the real AXC3000** at 175 MHz CK (`results/ph3_hyperbus_bw_len*.json`,
  `results/reports/hyperbus_bw.md`) — this is a `kind: measured` number, strictly stronger evidence
  than the `kind: estimate` 250 MB/s the existing PH0 sweep used. Since `ph0_estimator.md` itself
  found FPS scales ~linearly with the assumed bandwidth in this memory-bound regime, a same-day,
  **desk-doable** next step is re-running `scripts/estimate.py --model ad-toycar --arch
  models/arch/AGX3_Performance.arch --membw 342` (and 337 for read) to produce an `ad-toycar`
  estimate re-based on the *measured* ceiling instead of the *assumed* 250 MB/s one — still
  `kind: estimate` (no board run of the model itself yet), but grounded in a measured input rather
  than an assumed one. This is a concrete, cheap correction this plan recommends doing regardless
  of anything else in this document.
- On-board execution of *any* model is still blocked structurally, per the README's own "Model
  classifying on-board: blocked" row and `docs/ph3_coredla_nextsteps.md`'s ordered items — most
  relevantly item 4, the undocumented CoreDLA CSR start/done handshake
  (`sw/host/smoke_infer.py`'s `NotImplementedError`). Nothing in this MLPerf-Tiny-adoption plan
  changes that; §1-3 above are model/data work that can and should proceed in parallel, but the
  first **measured** (not estimated) MLPerf-Tiny-style FPS/accuracy number on this board is gated on
  that separate PH3 handoff closing.

### Bottom line

The only MLPerf-Tiny-core task this repo can currently get *any* CoreDLA-compiled number for is
`ad-toycar` (AD), and that number is today `kind: estimate`, memory-bound, and based on an assumed
rather than measured bandwidth (fixable by the `--membw 342` rerun above). The other three core
tasks are compile-blocked pending either an Open-Division arch/graph change or a
numerically-verified Closed-Division-equivalent one (§3). Nothing here claims a measured MLPerf-Tiny
number exists yet — it does not.

---

## 5. Ordered step list

| # | Step | Marked | Proving artifact |
|---|---|---|---|
| 1 | `git ls-remote --tags https://github.com/mlcommons/tiny`; resolve the v1.4 tag → commit SHA | desk | resolved SHA recorded in the issue/PR |
| 2 | Fetch the 4 checkpoint paths at that SHA, `sha256sum`, diff against existing `models/downloads/*.manifest.json` (or a fresh fetch if none exists yet) | desk | sha256 match/mismatch table, one row per model |
| 3 | Pin `_SOURCE_COMMIT`/`_CHECKPOINT_URL` in `models/{dscnn,resnet8,vww,ad}.py` to the resolved SHA (not `master`); re-run `fetch_models.py`→`export_onnx.py`→`eval_fp32.py`; commit refreshed manifests | desk | updated `*.manifest.json` with a real SHA (not the literal `"mlcommons/tiny@master"`); `eval_fp32` result unchanged (match) or superseded with a new dated file + note (mismatch) |
| 4 | Re-run `scripts/estimate.py --model ad-toycar --arch models/arch/AGX3_Performance.arch --membw 342` (and `--membw 337`) using the *measured* HyperRAM ceiling instead of the assumed 250 MB/s | desk | new `results/ph0_ad-toycar-*-342mbps_<date>.json`, still `kind: estimate` |
| 5 | Locate the v1.3+ streaming DS-CNN checkpoint path in the tree at the resolved SHA | desk | filename/path recorded, or explicitly "not found, needs escalation" if MLCommons hasn't published it in a discoverable location |
| 6 | If found, add `models/dscnn_streaming.py` (ModelSpec pattern) + registry entry; run fetch/export/eval_fp32 | desk | `results/ph2_ds-cnn-streaming-kws-fp32_<date>.json` |
| 7 | Draft the record-format/scoreboard delta needed for a *stateful* streaming model (PLAN §6 assumes single-shot per record) | desk | design note (this doc's §2 item 5, or a spun-off follow-up issue) |
| 8 | Run `dla_compiler --fanalyze-performance` on the new streaming model against `AGX3_Performance.arch` | needs-quartus | pass/fail row appended to a `ph0_sweep_attempts.csv`-style log |
| 9 | Numerically verify (or rule out) a Closed-Division-equivalent op decomposition for the DS-CNN/ResNet-8 pool-stride failures and the ResNet-8/VWW Transpose-placement failure (§3) | needs-quartus | diff-to-zero (or documented non-zero delta) between reference-graph and patched-graph outputs on identical test vectors |
| 10 | Once `docs/ph3_coredla_nextsteps.md`'s remaining items close (notably the CSR handshake), run `ad-toycar` end-to-end via the §6 record-replay harness on the real AXC3000 | needs-hardware | first `kind: measured`, `level: L5` result JSON for an actual MLPerf-Tiny-core task |
| 11 | A Closed-Division-legitimate on-board number for `ds-cnn-kws`/`resnet8-cifar10`/`mobilenetv1-025-vww` | impossible today | blocked until step 9 produces a verified-equivalent decomposition, or a non-vendor-modified arch relaxes the limits (which would itself only be Closed-eligible if step 9's equivalence check passes) |

---

## Sources

- https://github.com/mlcommons/tiny
- https://mlcommons.org/benchmarks/inference-tiny/
- MLCommons Tiny v1.4 results page (announced 2026-07-07)
- arXiv:2206.11791 — MLPerf Tiny paper (cited in `docs/PLAN.md` §11)
- This repo: `docs/PLAN.md` §2/§5/§9, `README.md`, `sw/model_prep/README.md`,
  `sw/model_prep/models/{dscnn,resnet8,vww,ad}.py`, `sw/model_prep/models/__init__.py`,
  `models/arch/README.md`, `results/reports/ph0_estimator.md`, `docs/ph3_coredla_nextsteps.md`,
  `results/schema/result.schema.json`
- **Caveat repeated for the record:** v1.4-specific reference-model deltas (whether the four core
  checkpoints changed bytes between whatever `master` state this repo originally fetched and the
  v1.4 tag, and the exact path of the v1.3+ streaming DS-CNN checkpoint) were **not confirmed** by
  web search at the time this plan was written. §1 step 1-2 and §2 step 1 are the exact, cheap way
  to close that gap — do them before treating any part of §1/§2 as settled.
