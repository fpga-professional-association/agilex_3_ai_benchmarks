#!/usr/bin/env python3
"""Evaluate fp32 ONNX accuracy on the exact MLPerf Tiny test sets (issue #2).

Runs each model's ONNX export through ``onnxruntime`` against its full test set (dataset fetched
first if not already present) and writes one ``results/`` JSON per model (``kind: "reference"``,
``level: "PH2"``). Tiny-YOLOv3 has no accuracy eval in v1 scope and is skipped.

    python eval_fp32.py                          # Tiny four + both large models
    python eval_fp32.py --models ad-toycar
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import common
from models import REGISTRY

DATE = "2026-07-04"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    evaluable = sorted(mid for mid, spec in REGISTRY.items() if spec.eval_fp32 is not None)
    ap.add_argument("--models", default=None,
                     help=f"comma-separated model ids (default: all evaluable). Choices: {evaluable}")
    ap.add_argument("--onnx-dir", default=str(common.ONNX_DIR))
    ap.add_argument("--datasets-dir", default=str(common.DATASETS_DIR))
    ap.add_argument("--results-dir", default=str(common.RESULTS_DIR))
    ap.add_argument("--date", default=DATE)
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else evaluable
    onnx_dir = Path(args.onnx_dir)
    datasets_dir = Path(args.datasets_dir)
    results_dir = Path(args.results_dir)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        spec = REGISTRY[model_id]
        if spec.eval_fp32 is None:
            print(f"[{model_id}] no accuracy eval in v1 scope -- skipping")
            continue

        onnx_path = onnx_dir / f"{model_id}.onnx"
        if not onnx_path.exists():
            print(f"[{model_id}] {onnx_path} missing -- run export_onnx.py first", file=sys.stderr)
            return 1

        print(f"[{model_id}] fetching dataset (if needed) ...")
        spec.fetch_dataset(datasets_dir)

        print(f"[{model_id}] evaluating fp32 accuracy ...")
        outcome = spec.eval_fp32(onnx_path, datasets_dir)
        metrics = outcome["metrics"]
        notes = outcome.get("notes", "")

        config = {
            "device": "A3CY100BM16AE7S",
            "board": "Arrow AXC3000",
            "model": model_id,
            "quantization": "fp32",
            "tool_versions": common.tool_versions("tensorflow", "torch", "torchvision", "onnx", "onnxruntime"),
        }
        result_path = results_dir / f"ph2_{model_id}-fp32_{args.date.replace('-', '')}.json"
        common.write_result(
            result_path,
            kind="reference",
            level="PH2",
            subject=f"{model_id}-fp32",
            date=args.date,
            plan_ref="§5 table",
            config=config,
            metrics=metrics,
            notes=notes,
        )
        print(f"[{model_id}] metrics={metrics} -> wrote {result_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
