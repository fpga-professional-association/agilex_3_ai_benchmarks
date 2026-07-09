"""MobileNetV2-1.0 and ResNet-18, ImageNet-1K pretrained (torchvision), issue #2.

These are the two >400 KB-class models we run a real fp32 accuracy check for (issue #2 only
requires >=1; both are cheap once the eval harness exists). Checkpoints are torchvision's own
ImageNet1K_V1 pretrained weights (downloaded on first use, cached under
``models/downloads/torch_home``) — no separate URL to pin, torchvision does its own integrity
check.

Accuracy is measured on **ImageNetV2 "matched-frequency"** (Recht et al. 2019), not the original
ImageNet validation set: the real ILSVRC val set requires an approved image-net.org account
(HuggingFace's ``ILSVRC/imagenet-1k`` mirror is access-gated behind the same terms), which this
pipeline cannot clear non-interactively. ImageNetV2 is class-balanced (10 images/class, 10,000
total), uses the same 1000 class indices, and is the standard ungated drop-in for a real top-1
sanity check -- but it is a harder, distribution-shifted set, so its top-1 numbers read several
points below torchvision's reported clean-val figures. That gap is expected, not a bug; it is
called out in the result JSON's notes every time.
"""

from __future__ import annotations

import os
import tarfile
from pathlib import Path

import numpy as np

import common

_DATASET_URL = (
    "https://huggingface.co/datasets/vaishaal/ImageNetV2/resolve/main/"
    "imagenetv2-matched-frequency.tar.gz"
)
# A *different* ImageNetV2 variant, used only for calibration -- never overlaps the eval images.
_CALIBRATION_DATASET_URL = (
    "https://huggingface.co/datasets/vaishaal/ImageNetV2/resolve/main/"
    "imagenetv2-threshold0.7.tar.gz"
)
IMAGE_SIZE = 224
CALIBRATION_SEED = 1234
CALIBRATION_SIZE = 300

ARCHES = {
    "mobilenetv2": {
        "model_id": "mobilenetv2-1.0-imagenet",
        "expected_weight_bytes": int(3.54 * 1024 * 1024),  # PLAN §5
        "clean_val_top1": 0.71878,  # torchvision IMAGENET1K_V1 model card
    },
    "resnet18": {
        "model_id": "resnet18-imagenet",
        "expected_weight_bytes": int(11.69 * 1024 * 1024),  # PLAN §5
        "clean_val_top1": 0.69758,  # torchvision IMAGENET1K_V1 model card
    },
}


def _set_torch_home() -> Path:
    """Point torchvision's own checkpoint cache at models/downloads/torch_home (not ~/.cache).

    Called unconditionally from ``_build_model`` — every entry point (fetch_checkpoint,
    export_onnx, eval_fp32) instantiates the model via ``_build_model``, and each runs in its own
    process (separate CLI invocations), so setting ``os.environ`` in only one of them wouldn't
    stick for the others.
    """
    torch_home = common.DOWNLOADS_DIR / "torch_home"
    torch_home.mkdir(parents=True, exist_ok=True)
    os.environ["TORCH_HOME"] = str(torch_home)
    return torch_home


def _build_model(arch: str):
    _set_torch_home()
    import torchvision

    if arch == "mobilenetv2":
        weights = torchvision.models.MobileNet_V2_Weights.IMAGENET1K_V1
        model = torchvision.models.mobilenet_v2(weights=weights)
    elif arch == "resnet18":
        weights = torchvision.models.ResNet18_Weights.IMAGENET1K_V1
        model = torchvision.models.resnet18(weights=weights)
    else:
        raise ValueError(f"unknown arch {arch!r}")
    model.eval()
    return model, weights


def fetch_checkpoint(dest_dir: Path, arch: str) -> Path:
    # dest_dir is accepted for ModelSpec interface conformance but unused: torchvision's cache
    # location must be fixed process-wide (see _set_torch_home), not per-call.
    del dest_dir
    _build_model(arch)  # triggers torchvision's own cached download + hash check
    torch_home = Path(os.environ["TORCH_HOME"])
    checkpoints = sorted((torch_home / "hub" / "checkpoints").glob("*.pth"))
    if not checkpoints:
        raise RuntimeError(f"torchvision did not cache a checkpoint under {torch_home}")
    # arch-specific file: torchvision names them e.g. mobilenet_v2-*.pth / resnet18-*.pth
    prefix = "mobilenet_v2" if arch == "mobilenetv2" else "resnet18"
    matches = [p for p in checkpoints if p.name.startswith(prefix)]
    return matches[0] if matches else checkpoints[0]


def fetch_dataset(dest_dir: Path) -> Path:
    """Download+extract ImageNetV2 matched-frequency (eval set) into ``dest_dir/imagenetv2``."""
    root = dest_dir / "imagenetv2"
    extracted = root / "imagenetv2-matched-frequency-format-val"
    if not extracted.exists():
        tar_path = root / "imagenetv2-matched-frequency.tar.gz"
        common.download(_DATASET_URL, tar_path)
        with tarfile.open(tar_path) as tf:
            tf.extractall(root)
        # some releases of the archive extract directly to a differently-named top dir; find it.
        if not extracted.exists():
            candidates = [p for p in root.iterdir() if p.is_dir()]
            if len(candidates) == 1:
                candidates[0].rename(extracted)
    return extracted


def _fetch_calibration_dataset(dest_dir: Path) -> Path:
    """Download+extract the ImageNetV2 threshold0.7 variant -- calibration only, distinct images
    from the matched-frequency eval set ``fetch_dataset`` returns."""
    root = dest_dir / "imagenetv2" / "imagenetv2-threshold0.7"
    extracted = root / "imagenetv2-threshold0.7-format-val"
    if not extracted.exists():
        tar_path = root / "imagenetv2-threshold0.7.tar.gz"
        common.download(_CALIBRATION_DATASET_URL, tar_path)
        with tarfile.open(tar_path) as tf:
            tf.extractall(root)
    return extracted


def export_onnx(checkpoint_path: Path, onnx_dir: Path, arch: str) -> tuple[Path, "common.ModelManifest"]:
    import torch

    model, _weights = _build_model(arch)
    info = ARCHES[arch]
    onnx_path = onnx_dir / f"{info['model_id']}.onnx"
    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.zeros(1, 3, IMAGE_SIZE, IMAGE_SIZE, dtype=torch.float32)
    torch.onnx.export(
        model, dummy, str(onnx_path),
        input_names=["input"], output_names=["logits"], opset_version=17,
        dynamo=False,
    )
    manifest = common.ModelManifest(
        model_id=info["model_id"],
        source_url=f"torchvision.models.{arch}(weights=IMAGENET1K_V1)",
        source_commit=common.tool_versions("torchvision").get("torchvision", "unknown"),
        sha256=common.sha256_file(onnx_path),
        param_count=common.param_count_from_onnx(onnx_path),
        input_shape=[1, 3, IMAGE_SIZE, IMAGE_SIZE],
        layout="NCHW",
        preprocessing="Resize(256)->CenterCrop(224)->ToTensor->Normalize(ImageNet mean/std), via weights.transforms()",
        opset=17,
        tool_versions=common.tool_versions("torch", "torchvision", "onnx"),
    )
    return onnx_path, manifest


def calibration_samples(dataset_dir: Path, arch: str) -> list:
    """300 random images from the ImageNetV2 threshold0.7 variant -- distinct from the eval set."""
    from PIL import Image

    _model, weights = _build_model(arch)
    transform = weights.transforms()
    calib_root = _fetch_calibration_dataset(dataset_dir)

    paths = []
    for class_dir in sorted(calib_root.iterdir(), key=lambda p: int(p.name)):
        paths.extend(sorted(class_dir.glob("*")))
    rng = np.random.default_rng(CALIBRATION_SEED)
    idx = sorted(rng.choice(len(paths), size=CALIBRATION_SIZE, replace=False))

    samples = []
    for i in idx:
        with Image.open(paths[i]) as im:
            tensor = transform(im.convert("RGB")).unsqueeze(0).numpy().astype(np.float32)
        samples.append(tensor)
    return samples


def eval_with_predictor(predict_fn, dataset_dir: Path, arch: str) -> dict:
    from PIL import Image

    _model, weights = _build_model(arch)
    transform = weights.transforms()
    val_root = fetch_dataset(dataset_dir)

    correct = 0
    n = 0
    class_dirs = sorted(val_root.iterdir(), key=lambda p: int(p.name))
    for class_dir in class_dirs:
        label = int(class_dir.name)
        for img_path in sorted(class_dir.glob("*")):
            with Image.open(img_path) as im:
                im = im.convert("RGB")
                tensor = transform(im).unsqueeze(0).numpy().astype(np.float32)
            logits = predict_fn(tensor)
            pred = int(np.argmax(logits[0]))
            correct += int(pred == label)
            n += 1

    accuracy = correct / n if n else 0.0
    info = ARCHES[arch]
    return {
        "metrics": {"accuracy_top1": accuracy, "n_records": n},
        "notes": (
            f"ImageNetV2 'matched-frequency' ({n} images, 1000 classes, class-balanced) -- NOT "
            "the original ILSVRC val set (gated behind an image-net.org account approval this "
            f"pipeline can't clear non-interactively). torchvision reports {info['clean_val_top1']:.3%} "
            "clean-val top-1 for this checkpoint; ImageNetV2 numbers run several points lower due "
            "to distribution shift (Recht et al. 2019) -- expected, not an export bug."
        ),
    }


def eval_fp32(onnx_path: Path, dataset_dir: Path, arch: str) -> dict:
    return eval_with_predictor(common.make_onnxruntime_predictor(onnx_path), dataset_dir, arch)
