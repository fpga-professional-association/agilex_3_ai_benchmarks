#!/usr/bin/env python3
"""Fetch the test-set datasets for the accuracy-evaluated models (issue #2).

Speech Commands v2 (KWS), CIFAR-10 (ResNet-8), the Silicon Labs COCO2014-derived 96x96 mirror
(VWW), the DCASE2020 ToyCar dev set (AD), and ImageNetV2 matched-frequency (MobileNetV2/ResNet-18)
— see each ``sw/model_prep/models/*.py`` module docstring for exact sources/licenses. Tiny-YOLoV3
has no dataset (informational-only model, PLAN §9 PH2 step 5) and is skipped. Idempotent.

    python fetch_datasets.py
    python fetch_datasets.py --models ds-cnn-kws,resnet8-cifar10
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
                     help=f"comma-separated model ids (default: all with a dataset). Choices: {sorted(REGISTRY)}")
    ap.add_argument("--out", default=str(common.DATASETS_DIR), help="dataset download dir")
    args = ap.parse_args(argv)

    if args.models:
        model_ids = args.models.split(",")
    else:
        model_ids = [mid for mid, spec in REGISTRY.items() if spec.fetch_dataset is not None]
    dest_dir = Path(args.out)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        spec = REGISTRY[model_id]
        if spec.fetch_dataset is None:
            print(f"[{model_id}] no dataset (informational-only model) -- skipping")
            continue
        print(f"[{model_id}] fetching dataset ...")
        path = spec.fetch_dataset(dest_dir)
        print(f"[{model_id}] dataset -> {path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
