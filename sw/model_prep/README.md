# sw/model_prep/

Model pipeline (PLAN §9 PH2): fetch/reproduce the MLPerf Tiny four (DS-CNN KWS, ResNet-8 CIFAR-10,
MobileNetV1-0.25 VWW, AD autoencoder) plus the >400 KB set (MobileNetV2, ResNet-18, Tiny-YOLOv3) →
ONNX → OpenVINO IR (2024.6) → NNCF post-training INT8 → accuracy references (fp32 and CPU-INT8).

Outputs land in gitignored `models/{downloads,onnx,ir}/` and `datasets/`; accuracy references land
in `results/` as `kind: "reference"` JSON. Every artifact gets a manifest with source URL/commit,
hashes, versions.

## Issue #2: model zoo (fp32)

```
pip install -r sw/model_prep/requirements.txt   # heavy — not installed by CI, see requirements.txt
cd sw/model_prep
python fetch_models.py         # checkpoints -> models/downloads/ (all committed upstream, no training)
python fetch_datasets.py       # test sets -> datasets/
python export_onnx.py          # -> models/onnx/<id>.onnx + <id>.manifest.json
python eval_fp32.py            # -> results/ph2_<id>-fp32_<date>.json
```

`models/` is a small registry (`models/__init__.py`) of `common.ModelSpec` entries, one per model
id; each of `models/{dscnn,resnet8,vww,ad,imagenet,yolov3tiny}.py` documents its exact checkpoint
source and dataset in its module docstring, including any deviations from the naive path and
*why* (e.g. a slow origin host swapped for a mirror, a lossy-JPEG source rejected after it
measurably hurt accuracy — see `models/resnet8.py`). Tiny-YOLOv3 is informational-only (PLAN §9
PH2 step 5: no detection accuracy in v1 scope) and has no dataset/eval.

All four MLPerf Tiny checkpoints are the fp32 TFLite files committed in `mlcommons/tiny` — no
training happens here. `export_onnx.py` sanity-checks the exported ONNX param count against PLAN
§5's INT8 weight-byte figure (±10%); models with many BatchNorm layers legitimately fall outside
that band because TFLite's converter fuses BatchNorm into the preceding Conv at export time — the
manifest's `notes` field always explains a flagged delta rather than silently reporting it.

## Issue #3: quantization pipeline (INT8)

```
python convert_ir.py           # models/onnx/<id>.onnx -> models/ir/<id>/fp32/<id>.{xml,bin}
python quantize_int8.py        # NNCF PTQ, 300-sample calibration -> models/ir/<id>/int8/<id>.{xml,bin}
python eval_int8_cpu.py        # -> results/ph2_<id>-int8_<date>.json (fp32->int8 delta noted)
python extract_quant_manifest.py  # -> models/ir/<id>/quant_manifest.json (scale/zero_point for sw/packer/)
```

Calibration is always a fixed-seed, ~300-sample slice from each model's **training** data — never
the eval/test split (`models/{dscnn,resnet8,vww,ad,imagenet}.py:calibration_samples()`; the two
ImageNet models calibrate off a *different* ImageNetV2 variant, `threshold0.7`, so calibration and
eval never touch the same images). Tiny-YOLOv3 has no established preprocessing/accuracy pipeline
(informational-only, PLAN §9 PH2 step 5) and is quantized with synthetic noise instead — flagged
loudly, never presented as validated.

`eval_int8_cpu.py` reuses the *exact same* `eval_with_predictor()` each model exposes for
`eval_fp32.py` — only the backend differs (OpenVINO compiled model vs onnxruntime) — so a
preprocessing bug can't silently split the two baselines (issue #3 step 3's requirement).

Two real bugs were found and fixed while measuring INT8 accuracy, not accepted at face value:

1. **AD calibration lacked diversity.** An early version sampled 10 ToyCar training files but,
   because consecutive frame-vectors within one file overlap 4/5 (stride-1 sliding window), the
   first ~2 files alone filled the whole 300-sample calibration budget — the other 8 sampled files
   were never used. AUC dropped from 0.876 (fp32) to 0.757, ~12 points versus the ≤1-2 points this
   issue expects. Fixed by spreading the same 300-sample budget across 60 files (a few
   evenly-spaced vectors each) instead of packing it from the first couple of files
   (`models/ad.py:calibration_samples`).
2. **ImageNet eval directory picked non-deterministically.** Adding the `threshold0.7` calibration
   variant next to the `matched-frequency` eval set left two candidate directories under
   `datasets/imagenetv2/`; `eval_with_predictor`'s old "first directory found" fallback logic
   could pick either one. Fixed by calling the already-correct `fetch_dataset()` directly instead
   of re-deriving the path. Verified no regression: re-ran `eval_fp32.py` for both ImageNet models
   after the fix and got byte-identical results to the pre-fix numbers already in the issue #2 PR.

Even after fixing the calibration bug, AD's INT8 AUC still lands ~0.78-0.83 (fp32: 0.876) — outside
the ≤1-2 point band the issue names. This is not treated as an unresolved bug: a direct sanity
check (fp32 IR run through OpenVINO reproduces the onnxruntime fp32 AUC to within normal
cross-backend floating-point variation, ruling out an eval-harness bug) plus the nature of the
metric explain it — AUC on reconstruction-error anomaly detection is measuring a *small residual*
between normal and anomalous inputs, and INT8 quantization noise is added directly into that same
residual, unlike classification accuracy which only needs the correct logit to stay the argmax.
Reported as a measured finding in the result JSON's `notes`, not smoothed over.
