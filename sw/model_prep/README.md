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

Issue #3 (OpenVINO IR + NNCF INT8 + CPU-INT8 reference, pinned to OpenVINO 2024.6) builds on these
fp32 ONNX exports and lands in a follow-up PR.
