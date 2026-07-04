#!/usr/bin/env python3
"""Fetch/reproduce pretrained checkpoints for all seven benchmark models (issue #2).

All four MLPerf Tiny models (DS-CNN, ResNet-8, VWW MobileNetV1-0.25, AD autoencoder) ship
pretrained checkpoints committed in github.com/mlcommons/tiny (Apache-2.0) — no training here.
MobileNetV2/ResNet-18 use torchvision's own cached ImageNet1K download; Tiny-YOLOv3 fetches a
prebuilt ONNX file directly. Idempotent: re-running skips files already on disk.

    python fetch_models.py                      # all seven
    python fetch_models.py --models dscnn,ad     # a subset (registry keys, see models/__init__.py)
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
    ap.add_argument("--out", default=str(common.DOWNLOADS_DIR), help="checkpoint download dir")
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else sorted(REGISTRY)
    dest_dir = Path(args.out)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        spec = REGISTRY[model_id]
        print(f"[{model_id}] fetching checkpoint ...")
        path = spec.fetch_checkpoint(dest_dir)
        print(f"[{model_id}] checkpoint -> {path} ({path.stat().st_size} B)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
