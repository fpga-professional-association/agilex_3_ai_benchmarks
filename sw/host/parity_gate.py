#!/usr/bin/env python3
"""Per-record accuracy parity gate: hardware predictions vs OpenVINO CPU-INT8 (issue #21).

PLAN §9 PH5 / §10 risk register: hardware accuracy must match the CPU-INT8 reference **per
record** on identical inputs -- an aggregate match doesn't rule out compensating errors (e.g. a
layout transpose that scrambles predictions but happens to preserve the overall pass rate).

The comparison logic (``compare_predictions``) is pure and fully offline-testable (no model, no
board -- see tests/test_parity_gate.py's mock hw-log). ``run_parity_gate`` is the hardware-facing
glue: it decodes each record's packed INT8 tensor (sw/packer's format), dequantizes with the
quant_manifest's scale/zero-point, and re-runs it through the same OpenVINO INT8 IR
issue #3's eval_int8_cpu.py used -- so the "same records" guarantee (PLAN §5's packer contract)
extends all the way to the parity check.

    python parity_gate.py --recimg kws.recimg --model-ir models/ir/ds-cnn-kws/int8/ds-cnn-kws.xml \\
        --quant-manifest models/ir/ds-cnn-kws/quant_manifest.json --hw-log kws_hw_log.bin
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

_HOST_DIR = Path(__file__).resolve().parent
_PACKER_DIR = _HOST_DIR.parent / "packer"
_MODEL_PREP_DIR = _HOST_DIR.parent / "model_prep"
for _dir in (_PACKER_DIR, _MODEL_PREP_DIR):
    if str(_dir) not in sys.path:
        sys.path.insert(0, str(_dir))


def load_hw_log(path: Path, n_records: int) -> list[int]:
    data = Path(path).read_bytes()
    if len(data) < n_records:
        raise ValueError(f"{path}: {len(data)} B, need >= {n_records} (1 B/record)")
    return list(data[:n_records])


def compare_predictions(hw_preds: list[int], cpu_preds: list[int],
                        cpu_top2_margins: list[float] | None = None,
                        max_mismatches: int = 20) -> dict:
    """Pure per-record comparison -- no model, no I/O. The offline-testable core of the gate.

    Returns ``{match_rate, n_records, n_mismatches, mismatches}``; ``mismatches`` holds up to
    ``max_mismatches`` entries of ``{index, hw_pred, cpu_pred, cpu_top2_margin}`` (the last key
    only present when ``cpu_top2_margins`` is given) -- margin near zero is the KWS/#21 tie-break
    signature (docs/parity_debugging.md).
    """
    if len(hw_preds) != len(cpu_preds):
        raise ValueError(f"hw_preds ({len(hw_preds)}) and cpu_preds ({len(cpu_preds)}) length mismatch")
    n = len(hw_preds)
    mismatches = []
    match_count = 0
    for k in range(n):
        if hw_preds[k] == cpu_preds[k]:
            match_count += 1
            continue
        if len(mismatches) < max_mismatches:
            entry = {"index": k, "hw_pred": int(hw_preds[k]), "cpu_pred": int(cpu_preds[k])}
            if cpu_top2_margins is not None:
                entry["cpu_top2_margin"] = float(cpu_top2_margins[k])
            mismatches.append(entry)
    return {
        "match_rate": match_count / n if n else 1.0,
        "n_records": n,
        "n_mismatches": n - match_count,
        "mismatches": mismatches,
    }


def _top1_and_margin(logits: np.ndarray) -> tuple[int, float]:
    order = np.argsort(logits)
    pred = int(order[-1])
    margin = float(logits[order[-1]] - logits[order[-2]]) if len(logits) >= 2 else float("inf")
    return pred, margin


def compute_cpu_predictions(recimg_path: Path, model_ir_path: Path,
                            quant_manifest_path: Path) -> tuple[list[int], list[float]]:
    """Re-run every record in ``recimg_path`` through the OpenVINO INT8 IR (CPU-INT8 reference)."""
    import common as model_prep_common
    import inspect_recimg

    image, manifest = inspect_recimg.load(recimg_path)
    qm = json.loads(Path(quant_manifest_path).read_text())
    scale, zero_point = qm["scale"], qm["zero_point"]
    input_shape = qm["input_shape"]  # includes the leading batch dim, e.g. [1, 49, 10, 1]

    predict_fn = model_prep_common.make_openvino_predictor(model_ir_path)
    preds, margins = [], []
    for k in range(manifest["record_count"]):
        tensor_i8, _label = inspect_recimg.decode(image, manifest, k)
        x = (tensor_i8.astype(np.float32) - zero_point) * scale
        x = x.reshape(input_shape)
        logits = predict_fn(x)[0]
        pred, margin = _top1_and_margin(logits)
        preds.append(pred)
        margins.append(margin)
    return preds, margins


def run_parity_gate(recimg_path: Path, model_ir_path: Path, quant_manifest_path: Path,
                    hw_log_path: Path, *, max_mismatches: int = 20) -> dict:
    import inspect_recimg

    _image, manifest = inspect_recimg.load(recimg_path)
    hw_preds = load_hw_log(hw_log_path, manifest["record_count"])
    cpu_preds, cpu_margins = compute_cpu_predictions(recimg_path, model_ir_path, quant_manifest_path)
    return compare_predictions(hw_preds, cpu_preds, cpu_margins, max_mismatches=max_mismatches)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recimg", required=True, type=Path)
    ap.add_argument("--model-ir", required=True, type=Path, help="OpenVINO INT8 IR .xml (issue #3)")
    ap.add_argument("--quant-manifest", required=True, type=Path, help="models/ir/<id>/quant_manifest.json")
    ap.add_argument("--hw-log", required=True, type=Path, help="raw predicted-class bytes (read_result_log.py)")
    ap.add_argument("--max-mismatches", type=int, default=20)
    args = ap.parse_args(argv)

    result = run_parity_gate(args.recimg, args.model_ir, args.quant_manifest, args.hw_log,
                             max_mismatches=args.max_mismatches)

    print(f"match rate: {result['match_rate']:.4%} ({result['n_records'] - result['n_mismatches']}/"
          f"{result['n_records']})")
    for m in result["mismatches"]:
        print(f"  record {m['index']}: hw={m['hw_pred']} cpu={m['cpu_pred']} "
              f"cpu_top2_margin={m.get('cpu_top2_margin', 'n/a')}")

    return 0 if result["match_rate"] >= 1.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
