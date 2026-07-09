#!/usr/bin/env python3
"""NNCF post-training INT8 quantization of each model's fp32 IR (issue #3 step 2).

Calibration is a fixed-seed, 300-sample slice from each model's *training* data (never the eval
split -- see each model module's ``calibration_samples()`` for the exact source/seed). Writes
``models/ir/<model_id>/int8/<model_id>.{xml,bin}``.

Tiny-YOLOv3 has no established preprocessing/accuracy pipeline (informational-only, PLAN §9 PH2
step 5) and no real calibration data source, so it's quantized with synthetic uniform-noise inputs
in its documented value range -- flagged in the log and never presented as a validated result
(there is no accuracy claim attached to it either way).

    python quantize_int8.py                          # all seven
    python quantize_int8.py --models resnet8-cifar10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

import common
from models import REGISTRY, yolov3tiny

CALIBRATION_SEED = 1234


def _synthetic_yolo_calibration(n: int = 32) -> list:
    """Uniform [0,1] noise at the documented I/O shapes (see yolov3tiny.py) -- no real dataset."""
    rng = np.random.default_rng(CALIBRATION_SEED)
    samples = []
    for _ in range(n):
        image = rng.uniform(0.0, 1.0, size=yolov3tiny.INPUT_SHAPE).astype(np.float32)
        image_shape = np.array([[416, 416]], dtype=np.float32)
        samples.append({"input_1": image, "image_shape": image_shape})
    return samples


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default=None,
                     help=f"comma-separated model ids (default: all). Choices: {sorted(REGISTRY)}")
    ap.add_argument("--ir-dir", default=str(common.IR_DIR))
    ap.add_argument("--datasets-dir", default=str(common.DATASETS_DIR))
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else sorted(REGISTRY)
    ir_dir = Path(args.ir_dir)
    datasets_dir = Path(args.datasets_dir)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        spec = REGISTRY[model_id]
        fp32_ir = ir_dir / model_id / "fp32" / f"{model_id}.xml"
        if not fp32_ir.exists():
            print(f"[{model_id}] {fp32_ir} missing -- run convert_ir.py first", file=sys.stderr)
            return 1

        if spec.calibration_samples is not None:
            print(f"[{model_id}] gathering calibration samples ...")
            calib = spec.calibration_samples(datasets_dir)
        else:
            print(f"[{model_id}] WARNING: no real calibration pipeline -- using synthetic noise "
                  f"(informational-only model, no accuracy claim attached to its INT8 IR)")
            calib = _synthetic_yolo_calibration()

        print(f"[{model_id}] quantizing ({len(calib)} calibration samples) ...")
        int8_ir = ir_dir / model_id / "int8" / f"{model_id}.xml"
        common.quantize_ir_int8(fp32_ir, int8_ir, calib)
        print(f"[{model_id}] wrote {int8_ir}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
