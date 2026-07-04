#!/usr/bin/env python3
"""Convert each model's fp32 ONNX export to OpenVINO IR (issue #3 step 1).

Pinned to OpenVINO 2024.6 (AI Suite 25.x's tracked version, per sw/model_prep/requirements.txt —
do not float this). Writes ``models/ir/<model_id>/fp32/<model_id>.{xml,bin}``.

    python export_onnx.py && python convert_ir.py     # all seven
    python convert_ir.py --models resnet8-cifar10
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import common
from models import REGISTRY


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default=None,
                     help=f"comma-separated model ids (default: all). Choices: {sorted(REGISTRY)}")
    ap.add_argument("--onnx-dir", default=str(common.ONNX_DIR))
    ap.add_argument("--ir-dir", default=str(common.IR_DIR))
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else sorted(REGISTRY)
    onnx_dir = Path(args.onnx_dir)
    ir_dir = Path(args.ir_dir)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        onnx_path = onnx_dir / f"{model_id}.onnx"
        if not onnx_path.exists():
            print(f"[{model_id}] {onnx_path} missing -- run export_onnx.py first", file=sys.stderr)
            return 1
        print(f"[{model_id}] converting ONNX -> OpenVINO IR ...")
        xml_path = common.convert_onnx_to_ir(onnx_path, ir_dir / model_id / "fp32", model_id)
        print(f"[{model_id}] wrote {xml_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
