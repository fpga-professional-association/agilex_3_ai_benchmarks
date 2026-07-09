#!/usr/bin/env python3
"""Evaluate the quantize_sweep.py ladder's accuracy, per point, on the full CPU test set (issue #23).

Reuses each model's ``eval_with_predictor`` -- the exact same accuracy-computation code
eval_fp32.py/eval_int8_cpu.py call, just pointed at whichever ladder IR -- so a preprocessing bug
can't silently split the ladder points from #2/#3's baselines (same requirement issue #3 step 3
already established, issue #23 step 3 repeats it: "same test sets and metrics as #2/#3").

Writes one ``results/l5_quant-sweep_<model_id>-<config>_<date>.json`` per *achieved* ladder point
(``kind: "reference"``, ``level: "L5"`` -- PLAN §7 L5 is the "model corpus ... accuracy ... per
quantization point" level this issue's Pareto belongs to). Every JSON's ``config`` block carries
the exact NNCF settings ``quantize_sweep.py`` recorded, so the Pareto plot never needs a second,
possibly-drifted source of truth for what a config means.

    python eval_quant_sweep.py                          # every model with an achieved ladder point
    python eval_quant_sweep.py --models ad-toycar
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import common
from models import REGISTRY

DATE = "2026-07-09"


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

    wrote_any = False
    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        spec = REGISTRY[model_id]
        if spec.eval_with_predictor is None:
            print(f"[{model_id}] no accuracy eval in v1 scope -- skipping")
            continue

        manifest_path = ir_dir / model_id / "quant_sweep" / "manifest.json"
        if not manifest_path.exists():
            print(f"[{model_id}] {manifest_path} missing -- run quantize_sweep.py first", file=sys.stderr)
            continue
        manifest = json.loads(manifest_path.read_text())

        print(f"[{model_id}] fetching dataset (if needed) ...")
        spec.fetch_dataset(datasets_dir)

        for config_name, config_info in manifest["configs"].items():
            if not config_info.get("achieved"):
                print(f"[{model_id}] {config_name}: not achieved ({config_info.get('reason')}) -- skip eval")
                continue

            ir_path = Path(config_info["ir_path"])
            print(f"[{model_id}] {config_name}: evaluating accuracy ...")
            predict_fn = common.make_openvino_predictor(ir_path)
            outcome = spec.eval_with_predictor(predict_fn, datasets_dir)
            metrics = outcome["metrics"]
            notes = outcome.get("notes", "")
            if config_name == "int4-weight-only":
                notes = (
                    f"{notes} Weight-only NNCF compression (nncf.compress_weights) -- activations "
                    "and the matmul itself still execute fp32/fp16 on OpenVINO CPU, so this is a "
                    "real accuracy number but NOT a measured or achievable throughput point; "
                    "scripts/make_pareto.py projects its x-axis compute multiplier from issue "
                    "#10's L0b soft-logic MAC densities, not from this run."
                )

            config = {
                "device": "A3CY100BM16AE7S",
                "board": "Arrow AXC3000",
                "model": model_id,
                "quantization": config_name,
                "nncf_settings": config_info["nncf_settings"],
                "tool_versions": common.tool_versions("openvino", "nncf"),
            }
            subject = f"{model_id}-{config_name}"
            result_path = results_dir / f"l5_quant-sweep_{subject}_{args.date.replace('-', '')}.json"
            common.write_result(
                result_path,
                kind="reference",
                level="L5",
                subject=subject,
                date=args.date,
                plan_ref="§3 LV7 / §7 L5",
                config=config,
                metrics=metrics,
                notes=notes,
            )
            print(f"[{model_id}] {config_name}: metrics={metrics} -> wrote {result_path}")
            wrote_any = True

    if not wrote_any:
        print("no ladder points evaluated -- nothing written", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
