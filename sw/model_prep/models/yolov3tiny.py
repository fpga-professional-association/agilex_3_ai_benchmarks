"""Tiny-YOLOv3 416x416 (informational only), issue #2.

PLAN §5/§9 PH2: detection accuracy (mAP) is explicitly out of scope for v1 -- this model is
carried for its size/compute footprint only, so no ``eval_fp32`` is implemented (the CLI
dispatcher skips models with ``eval_fp32 is None``). The model is already ONNX (opset 11, from
the ONNX Model Zoo); ``export_onnx`` here is a fetch-and-validate copy, not a framework export.

Source: the ONNX Model Zoo's GitHub LFS hosting for this artifact was retired 2025-07-01; the
project's own migration target is the ``onnxmodelzoo`` HuggingFace org, which serves the same
byte-identical artifact (verified: HF's file matches the original LFS pointer's sha256 oid).
"""

from __future__ import annotations

from pathlib import Path

import common

MODEL_ID = "tiny-yolov3"
EXPECTED_WEIGHT_BYTES = int(8.86 * 1024 * 1024)  # PLAN §5

_ONNX_URL = "https://huggingface.co/onnxmodelzoo/tiny-yolov3-11/resolve/main/tiny-yolov3-11.onnx"
_SOURCE_COMMIT = "onnxmodelzoo/tiny-yolov3-11@main (ONNX Model Zoo migration target)"

INPUT_SHAPE = [1, 3, 416, 416]


def fetch_checkpoint(dest_dir: Path) -> Path:
    dest = dest_dir / "yolov3tiny" / "tiny-yolov3-11.onnx"
    common.download(_ONNX_URL, dest)
    return dest


def fetch_dataset(dest_dir: Path) -> Path:
    raise NotImplementedError(
        "tiny-yolov3 has no accuracy eval in v1 scope (PLAN §9 PH2 step 5) -- no dataset to fetch")


def export_onnx(checkpoint_path: Path, onnx_dir: Path) -> tuple[Path, "common.ModelManifest"]:
    onnx_path = onnx_dir / f"{MODEL_ID}.onnx"
    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    onnx_path.write_bytes(checkpoint_path.read_bytes())
    manifest = common.ModelManifest(
        model_id=MODEL_ID,
        source_url=_ONNX_URL,
        source_commit=_SOURCE_COMMIT,
        sha256=common.sha256_file(onnx_path),
        param_count=common.param_count_from_onnx(onnx_path),
        input_shape=INPUT_SHAPE,
        layout="NCHW",
        preprocessing="letterbox resize to 416x416, RGB, /255; image_shape=[orig_h,orig_w] side input",
        opset=11,
        tool_versions=common.tool_versions("onnx"),
        notes="No accuracy eval in v1 scope (informational model only, PLAN §9 PH2 step 5).",
    )
    return onnx_path, manifest


eval_fp32 = None  # informational only -- see module docstring
