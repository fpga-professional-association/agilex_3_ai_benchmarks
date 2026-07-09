"""Visual Wake Words, MobileNetV1-0.25 @ 96x96 (MLPerf Tiny), issue #2.

Checkpoint: fp32 TFLite export of mlcommons/tiny's ``vww_96.h5`` (MobileNetV1 alpha=0.25,
Apache-2.0) — no training here. Dataset: the Silicon Labs COCO2014-derived, pre-resized 96x96
mirror (``vw_coco2014_96.tar.gz``) that the reference ``train_vww.py`` itself downloads — COCO
annotations CC BY 4.0, images per-photographer Flickr licenses.

The reference pipeline has no separate held-out test set: ``train_vww.py`` carves 10% off the
same ``person``/``non_person`` directory tree at train time (``ImageDataGenerator(...,
validation_split=0.1)``). We reproduce that carve-out deterministically (sorted filenames per
class, last 10%) as our eval set — documented here rather than silently deviating.
"""

from __future__ import annotations

import tarfile
from pathlib import Path

import numpy as np

import common

MODEL_ID = "mobilenetv1-025-vww"
EXPECTED_WEIGHT_BYTES = int(325 * 1024)  # PLAN §5

_CHECKPOINT_URL = (
    "https://raw.githubusercontent.com/mlcommons/tiny/master/"
    "benchmark/training/visual_wake_words/trained_models/vww_96_float.tflite"
)
_SOURCE_COMMIT = "mlcommons/tiny@master"
_DATASET_URL = (
    "https://www.silabs.com/public/files/github/machine_learning/benchmarks/datasets/"
    "vw_coco2014_96.tar.gz"
)

IMAGE_SIZE = 96
VALIDATION_SPLIT = 0.1
# Alphabetical directory order == Keras flow_from_directory's class_indices.
CLASSES = ["non_person", "person"]


def fetch_checkpoint(dest_dir: Path) -> Path:
    dest = dest_dir / "vww" / "vww_96_float.tflite"
    common.download(_CHECKPOINT_URL, dest)
    return dest


def fetch_dataset(dest_dir: Path) -> Path:
    root = dest_dir / "vww"
    extracted = root / "vw_coco2014_96"
    if not extracted.exists():
        tar_path = root / "vw_coco2014_96.tar.gz"
        common.download(_DATASET_URL, tar_path)
        with tarfile.open(tar_path) as tf:
            tf.extractall(root)
    return extracted


def export_onnx(checkpoint_path: Path, onnx_dir: Path) -> tuple[Path, "common.ModelManifest"]:
    onnx_path = onnx_dir / f"{MODEL_ID}.onnx"
    common.convert_tflite_to_onnx(checkpoint_path, onnx_path, opset=13)
    manifest = common.ModelManifest(
        model_id=MODEL_ID,
        source_url=_CHECKPOINT_URL,
        source_commit=_SOURCE_COMMIT,
        sha256=common.sha256_file(onnx_path),
        param_count=common.param_count_from_onnx(onnx_path),
        input_shape=[1, IMAGE_SIZE, IMAGE_SIZE, 3],
        layout="NHWC",
        preprocessing="RGB, resized to 96x96, rescale 1/255 (matches upstream train_vww.py ImageDataGenerator)",
        opset=13,
        tool_versions=common.tool_versions("tensorflow", "tf2onnx", "onnx"),
    )
    return onnx_path, manifest


def _eval_file_list(dataset_root: Path) -> list[tuple[Path, int]]:
    """Deterministic last-10%-per-class split, mirroring train_vww.py's validation carve-out."""
    items: list[tuple[Path, int]] = []
    for class_idx, class_name in enumerate(CLASSES):
        files = sorted((dataset_root / class_name).glob("*.jpg"))
        n_val = max(1, int(round(len(files) * VALIDATION_SPLIT)))
        for path in files[-n_val:]:
            items.append((path, class_idx))
    return items


CALIBRATION_SEED = 1234
CALIBRATION_SIZE = 300


def _train_file_list(dataset_root: Path) -> list[tuple[Path, int]]:
    """The complementary first-90%-per-class split -- never overlaps _eval_file_list's last 10%."""
    items: list[tuple[Path, int]] = []
    for class_idx, class_name in enumerate(CLASSES):
        files = sorted((dataset_root / class_name).glob("*.jpg"))
        n_val = max(1, int(round(len(files) * VALIDATION_SPLIT)))
        for path in files[:len(files) - n_val]:
            items.append((path, class_idx))
    return items


def calibration_samples(dataset_dir: Path) -> list:
    """300 random images from the 90% training portion (fixed seed), disjoint from the eval set."""
    from PIL import Image

    dataset_root = dataset_dir / "vww" / "vw_coco2014_96"
    items = _train_file_list(dataset_root)
    rng = np.random.default_rng(CALIBRATION_SEED)
    idx = sorted(rng.choice(len(items), size=CALIBRATION_SIZE, replace=False))
    samples = []
    for i in idx:
        path, _label = items[i]
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE)), dtype=np.float32) / 255.0
        samples.append(arr[np.newaxis, ...])
    return samples


def eval_with_predictor(predict_fn, dataset_dir: Path) -> dict:
    from PIL import Image

    dataset_root = dataset_dir / "vww" / "vw_coco2014_96"
    items = _eval_file_list(dataset_root)

    correct = 0
    batch_imgs: list[np.ndarray] = []
    batch_labels: list[int] = []

    def flush():
        nonlocal correct
        if not batch_imgs:
            return
        batch = np.stack(batch_imgs).astype(np.float32) / 255.0
        logits = predict_fn(batch)
        preds = np.argmax(logits, axis=1)
        correct += int(np.sum(preds == np.array(batch_labels)))
        batch_imgs.clear()
        batch_labels.clear()

    for path, label in items:
        with Image.open(path) as im:
            im = im.convert("RGB").resize((IMAGE_SIZE, IMAGE_SIZE))
            batch_imgs.append(np.asarray(im))
        batch_labels.append(label)
        if len(batch_imgs) >= 128:
            flush()
    flush()

    n = len(items)
    accuracy = correct / n if n else 0.0
    return {
        "metrics": {"accuracy_top1": accuracy, "n_records": n},
        "notes": (
            f"Eval set = last {VALIDATION_SPLIT:.0%} of each class dir (sorted filenames), "
            f"{n} images total ({len(items)} across {len(CLASSES)} classes) -- the reference "
            "pipeline has no separately-labeled held-out test set (see module docstring). "
            "MLPerf Tiny reference ballpark: 80%+ top-1."
        ),
    }


def eval_fp32(onnx_path: Path, dataset_dir: Path) -> dict:
    return eval_with_predictor(common.make_onnxruntime_predictor(onnx_path), dataset_dir)
