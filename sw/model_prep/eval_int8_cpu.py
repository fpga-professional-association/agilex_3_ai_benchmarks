#!/usr/bin/env python3
"""Evaluate INT8 IR accuracy on CPU, same preprocessing/test sets as eval_fp32.py (issue #3 step 3).

Reuses each model's ``eval_with_predictor`` (the same accuracy-computation code eval_fp32.py
calls, just pointed at an OpenVINO INT8 predictor instead of onnxruntime) so a preprocessing bug
can't silently split the two baselines. Writes ``results/ph2_<id>-int8_<date>.json``
(``kind: "reference"``) with the fp32->INT8 delta noted.

    python eval_int8_cpu.py                          # Tiny four + both large models
    python eval_int8_cpu.py --models ad-toycar
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

import common
from models import REGISTRY

DATE = "2026-07-04"


def _latest_fp32_accuracy(results_dir: Path, model_id: str) -> tuple[float | None, str | None]:
    """Best-effort lookup of this model's fp32 reference JSON, for the delta note."""
    matches = sorted(glob.glob(str(results_dir / f"ph2_{model_id}-fp32_*.json")))
    if not matches:
        return None, None
    data = json.loads(Path(matches[-1]).read_text())
    metrics = data.get("metrics", {})
    for key in ("accuracy_top1", "auc"):
        if key in metrics:
            return metrics[key], key
    return None, None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    evaluable = sorted(mid for mid, spec in REGISTRY.items() if spec.eval_with_predictor is not None)
    ap.add_argument("--models", default=None,
                     help=f"comma-separated model ids (default: all evaluable). Choices: {evaluable}")
    ap.add_argument("--ir-dir", default=str(common.IR_DIR))
    ap.add_argument("--datasets-dir", default=str(common.DATASETS_DIR))
    ap.add_argument("--results-dir", default=str(common.RESULTS_DIR))
    ap.add_argument("--date", default=DATE)
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else evaluable
    ir_dir = Path(args.ir_dir)
    datasets_dir = Path(args.datasets_dir)
    results_dir = Path(args.results_dir)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        spec = REGISTRY[model_id]
        if spec.eval_with_predictor is None:
            print(f"[{model_id}] no accuracy eval in v1 scope -- skipping")
            continue

        int8_ir = ir_dir / model_id / "int8" / f"{model_id}.xml"
        if not int8_ir.exists():
            print(f"[{model_id}] {int8_ir} missing -- run quantize_int8.py first", file=sys.stderr)
            return 1

        print(f"[{model_id}] fetching dataset (if needed) ...")
        spec.fetch_dataset(datasets_dir)

        print(f"[{model_id}] evaluating INT8 accuracy ...")
        predict_fn = common.make_openvino_predictor(int8_ir)
        outcome = spec.eval_with_predictor(predict_fn, datasets_dir)
        metrics = outcome["metrics"]
        notes = outcome.get("notes", "")

        fp32_value, metric_key = _latest_fp32_accuracy(results_dir, model_id)
        if fp32_value is not None and metric_key in metrics:
            delta = metrics[metric_key] - fp32_value
            notes = f"{notes} fp32->int8 {metric_key} delta: {delta:+.4f} (fp32={fp32_value:.4f})."

        config = {
            "device": "A3CY100BM16AE7S",
            "board": "Arrow AXC3000",
            "model": model_id,
            "quantization": "int8-nncf-ptq",
            "tool_versions": common.tool_versions("openvino", "nncf"),
        }
        result_path = results_dir / f"ph2_{model_id}-int8_{args.date.replace('-', '')}.json"
        common.write_result(
            result_path,
            kind="reference",
            level="PH2",
            subject=f"{model_id}-int8",
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
