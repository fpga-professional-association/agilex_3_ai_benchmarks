"""ResNet-8 CIFAR-10 (MLPerf Tiny image classification), issue #2.

Checkpoint: the fp32 TFLite export of mlcommons/tiny's ``pretrainedResnet.h5``
(``resnet_v1_eembc(conv_filters=26)`` — 3 residual stacks, 26/52/104 filters). No training here:
the reference checkpoint is committed upstream (Apache-2.0). Dataset: the full CIFAR-10 test
split (10,000 images, 1000/class — matches docs/record_format.md exactly, not MLPerf's own
200-sample perf subset), fetched as a single Parquet file from the ``uoft-cs/cifar10`` Hugging
Face mirror (images stored as embedded lossless PNG bytes).

The canonical ``cs.toronto.edu`` host measured under 50 kB/s from this environment (170 MB would
take over an hour); this Parquet mirror serves the same 10,000-image split, in one ~24 MB file,
off HF's CDN in a couple seconds. A first attempt used a GitHub JPEG-image mirror instead
(``YoongiKim/CIFAR-10-images``) — same 10,000 images, same labels, same ~1s fetch — but measured
top-1 came out at 75.5%, well under the 85% target. Root-caused (not accepted at face value, per
AGENTS.md): JPEG's lossy re-compression measurably perturbs pixel values on 32x32 inputs, and this
tiny ResNet turned out to be sensitive enough to that noise to lose ~13 points. Direct A/B on a
2,000-image subset confirmed it: lossless-PNG source 87.9% vs the JPEG mirror's 72% on the exact
same images through the exact same checkpoint. Switched to this Parquet (lossless) source instead
of accepting the shortfall.

Preprocessing note: the upstream pipeline (``train.py:load_cifar_10_data``) feeds raw uint8 pixel
values (0..255) straight into the model with *no* rescaling layer — reproduced here verbatim.
"""

from __future__ import annotations

import io
from pathlib import Path

import numpy as np

import common

MODEL_ID = "resnet8-cifar10"
EXPECTED_WEIGHT_BYTES = int(78.7 * 1024)  # PLAN §5

_CHECKPOINT_URL = (
    "https://raw.githubusercontent.com/mlcommons/tiny/master/"
    "benchmark/training/image_classification/trained_models/pretrainedResnet.tflite"
)
_SOURCE_COMMIT = "mlcommons/tiny@master"
_DATASET_URL = "https://huggingface.co/api/datasets/uoft-cs/cifar10/parquet/plain_text/test/0.parquet"
_TRAIN_DATASET_URL = "https://huggingface.co/api/datasets/uoft-cs/cifar10/parquet/plain_text/train/0.parquet"
CALIBRATION_SEED = 1234
CALIBRATION_SIZE = 300

# Canonical CIFAR-10 label order (batches.meta label_names / torchvision.datasets.CIFAR10.classes
# / this HF mirror's int label 0..9) — no remapping needed.
CLASSES = [
    "airplane", "automobile", "bird", "cat", "deer",
    "dog", "frog", "horse", "ship", "truck",
]


def fetch_checkpoint(dest_dir: Path) -> Path:
    dest = dest_dir / "resnet8" / "pretrainedResnet.tflite"
    common.download(_CHECKPOINT_URL, dest)
    return dest


def fetch_dataset(dest_dir: Path) -> Path:
    """Download the CIFAR-10 test split (10,000 lossless-PNG rows) into ``dest_dir/cifar10``."""
    dest = dest_dir / "cifar10" / "test.parquet"
    common.download(_DATASET_URL, dest)
    return dest


def export_onnx(checkpoint_path: Path, onnx_dir: Path) -> tuple[Path, "common.ModelManifest"]:
    onnx_path = onnx_dir / f"{MODEL_ID}.onnx"
    common.convert_tflite_to_onnx(checkpoint_path, onnx_path, opset=13)
    manifest = common.ModelManifest(
        model_id=MODEL_ID,
        source_url=_CHECKPOINT_URL,
        source_commit=_SOURCE_COMMIT,
        sha256=common.sha256_file(onnx_path),
        param_count=common.param_count_from_onnx(onnx_path),
        input_shape=[1, 32, 32, 3],
        layout="NHWC",
        preprocessing="raw uint8 pixel values cast to float32, NO rescaling (matches upstream train.py)",
        opset=13,
        tool_versions=common.tool_versions("tensorflow", "tf2onnx", "onnx"),
    )
    return onnx_path, manifest


def fetch_train_dataset(dest_dir: Path) -> Path:
    """Download the CIFAR-10 *train* split (50,000 rows) -- calibration data only, never eval."""
    dest = dest_dir / "cifar10" / "train.parquet"
    common.download(_TRAIN_DATASET_URL, dest)
    return dest


def calibration_samples(dataset_dir: Path) -> list:
    """300 random train-split images (fixed seed) -- never drawn from the eval/test split."""
    import pyarrow.parquet as pq
    from PIL import Image

    parquet_path = fetch_train_dataset(dataset_dir)
    rows = pq.read_table(parquet_path).to_pylist()
    idx = np.random.default_rng(CALIBRATION_SEED).choice(len(rows), size=CALIBRATION_SIZE, replace=False)
    idx.sort()
    samples = []
    for i in idx:
        img = np.asarray(Image.open(io.BytesIO(rows[i]["img"]["bytes"])).convert("RGB"), dtype=np.float32)
        samples.append(img[np.newaxis, ...])
    return samples


def eval_with_predictor(predict_fn, dataset_dir: Path) -> dict:
    import pyarrow.parquet as pq
    from PIL import Image

    parquet_path = dataset_dir / "cifar10" / "test.parquet"
    table = pq.read_table(parquet_path)
    rows = table.to_pylist()

    correct = 0
    n = len(rows)
    batch = 256
    for start in range(0, n, batch):
        chunk = rows[start:start + batch]
        images = np.stack([
            np.asarray(Image.open(io.BytesIO(r["img"]["bytes"])).convert("RGB"), dtype=np.float32)
            for r in chunk
        ])
        labels = np.array([r["label"] for r in chunk], dtype=np.int64)
        logits = predict_fn(images)
        preds = np.argmax(logits, axis=1)
        correct += int(np.sum(preds == labels))

    accuracy = correct / n if n else 0.0
    return {
        "metrics": {"accuracy_top1": accuracy, "n_records": n},
        "notes": (
            f"Full CIFAR-10 test set ({n} images, 1000/class), matches docs/record_format.md's "
            "10,000-record count. Images from the uoft-cs/cifar10 HF Parquet mirror (lossless PNG "
            "bytes) -- see module docstring for why a JPEG mirror was rejected (measured ~13-point "
            "accuracy hit from lossy re-compression on this 32x32-input model). MLPerf Tiny "
            "reference reports ~87.0% top-1 on its own 200-sample perf subset; quality target "
            "85%+ top-1 on the full test set."
        ),
    }


def eval_fp32(onnx_path: Path, dataset_dir: Path) -> dict:
    return eval_with_predictor(common.make_onnxruntime_predictor(onnx_path), dataset_dir)
