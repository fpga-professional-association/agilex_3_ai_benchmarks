#!/usr/bin/env python3
"""Quantization ladder per model for the LV7 accuracy-vs-bits Pareto (issue #23, PLAN §3 LV7/§7 L5).

Produces, per model, up to three NNCF-quantized OpenVINO IRs from the *same* fp32 IR and the
*same* fixed calibration slice (issue #23 "Do not": never calibrate different ladder points on
different data):

  1. ``int8-per-channel`` -- NNCF post-training quantization, per-channel weight granularity
     (the #3/issue-3 default: plain ``nncf.quantize`` with no weight-granularity override).
  2. ``int8-per-tensor``  -- the same PTQ recipe, weight granularity forced to per-tensor via
     ``AdvancedQuantizationParameters(weights_quantization_params=QuantizationParameters(
     per_channel=False))``.
  3. ``int4-weight-only`` -- NNCF weight *compression* (``nncf.compress_weights``,
     ``mode=INT4_SYM``, ``group_size=-1`` i.e. one scale per output channel, ``all_layers=True``
     so every weight tensor including small ones is compressed, not just the "ratio-defining"
     large ones NNCF's LLM-oriented heuristic would otherwise skip). This is a *weight-only*
     technique -- activations and the matmul itself still run fp32/fp16 on OpenVINO CPU, so the
     accuracy point it produces is real, but the "effective compute" story for this point is a
     projection from #10's L0b soft-logic densities, never a claim that this software config runs
     any faster (see ``scripts/make_pareto.py``).

NNCF capability limits found while building this ladder (documented per issue #23 acceptance
criteria, not smoothed over):

  - ``nncf.quantize``'s ``mode`` override (full activation+weight PTQ at non-default numerics)
    only offers ``fp8_e4m3``/``fp8_e5m2`` in NNCF 2.14.1 -- there is no INT4/INT2/INT1 *activation*
    PTQ path at all for a generic ONNX/OpenVINO graph. Sub-8-bit activation quantization is not
    achievable with this pinned toolchain, full stop -- only weight-only compression is.
  - ``nncf.compress_weights``'s ``CompressWeightsMode`` enum offers int8_sym/int8_asym/int4_sym/
    int4_asym/nf4/int8/e2m1 -- no INT2 or INT1 mode exists. The software accuracy ladder therefore
    cannot go below INT4 on any config; #10's measured INT2/INT1 soft-MAC densities remain a
    hardware-RTL-only data point with no corresponding software accuracy point in this issue.
  - The default ``group_size=128`` grouped quantization fails outright
    (``nncf.errors.UnsupportedModelError: Channel size N should be divisible by size of group
    128``) on every Tiny model here -- their layers are far narrower than an LLM's (e.g. the AD
    autoencoder's 8-unit bottleneck). ``group_size=-1`` (per-output-channel, no grouping) is
    required to compress these graphs at all; documented here rather than silently retried.

Writes ``models/ir/<model_id>/quant_sweep/<config>/<model_id>.{xml,bin}`` (kept separate from
issue #3's canonical ``models/ir/<model_id>/int8/`` artifact, which other issues depend on) plus
one ``models/ir/<model_id>/quant_sweep/manifest.json`` recording, per config, whether it was
achieved and the exact NNCF settings used (or the failure reason if not).

    python quantize_sweep.py                          # every evaluable model
    python quantize_sweep.py --models ad-toycar
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import common
from models import REGISTRY

CALIBRATION_SUBSET_SIZE_CAP = 300  # matches issue #3's fixed calibration budget

# NNCF weight-compression settings shared by every model's int4-weight-only attempt.
INT4_GROUP_SIZE = -1  # per-output-channel (no grouping) -- see module docstring for why
INT4_ALL_LAYERS = True  # compress every weight tensor, not just NNCF's LLM-oriented "ratio-defining" subset


def _quantize_int8(fp32_ir: Path, out_xml: Path, calib: list, *, per_channel: bool) -> dict[str, Any]:
    import nncf
    import openvino as ov
    from nncf.quantization.advanced_parameters import (
        AdvancedQuantizationParameters,
        QuantizationParameters,
    )

    core = ov.Core()
    model = core.read_model(str(fp32_ir))
    dataset = nncf.Dataset(calib)
    advanced = None
    settings: dict[str, Any] = {
        "algorithm": "nncf.quantize (post-training, min-max + fast bias correction)",
        "subset_size": len(calib),
        "weights_per_channel": per_channel,
    }
    if not per_channel:
        advanced = AdvancedQuantizationParameters(
            weights_quantization_params=QuantizationParameters(per_channel=False)
        )
    quantized = nncf.quantize(model, dataset, subset_size=len(calib), advanced_parameters=advanced)
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    ov.save_model(quantized, str(out_xml))
    return settings


def _quantize_int4_weight_only(fp32_ir: Path, out_xml: Path) -> dict[str, Any]:
    import nncf
    import openvino as ov

    core = ov.Core()
    model = core.read_model(str(fp32_ir))
    settings: dict[str, Any] = {
        "algorithm": "nncf.compress_weights (weight-only, data-free)",
        "mode": "int4_sym",
        "group_size": INT4_GROUP_SIZE,
        "all_layers": INT4_ALL_LAYERS,
    }
    compressed = nncf.compress_weights(
        model,
        mode=nncf.CompressWeightsMode.INT4_SYM,
        group_size=INT4_GROUP_SIZE,
        all_layers=INT4_ALL_LAYERS,
    )
    out_xml.parent.mkdir(parents=True, exist_ok=True)
    ov.save_model(compressed, str(out_xml))
    return settings


LADDER = ["int8-per-channel", "int8-per-tensor", "int4-weight-only"]


def build_ladder(model_id: str, ir_dir: Path, datasets_dir: Path) -> dict[str, Any]:
    """Attempt every ladder config for one model; returns the manifest dict (never raises)."""
    spec = REGISTRY[model_id]
    fp32_ir = ir_dir / model_id / "fp32" / f"{model_id}.xml"
    sweep_dir = ir_dir / model_id / "quant_sweep"
    result: dict[str, Any] = {"model_id": model_id, "configs": {}}

    if not fp32_ir.exists():
        for cfg in LADDER:
            result["configs"][cfg] = {"achieved": False, "reason": f"{fp32_ir} missing -- run convert_ir.py first"}
        return result

    print(f"[{model_id}] gathering the fixed calibration slice (shared by every ladder point) ...")
    calib_full = spec.calibration_samples(datasets_dir)
    calib = calib_full[:CALIBRATION_SUBSET_SIZE_CAP]
    print(f"[{model_id}] {len(calib)} calibration samples (same slice for every config)")

    for cfg, per_channel in (("int8-per-channel", True), ("int8-per-tensor", False)):
        out_xml = sweep_dir / cfg / f"{model_id}.xml"
        try:
            settings = _quantize_int8(fp32_ir, out_xml, calib, per_channel=per_channel)
            settings["calibration_size"] = len(calib)
            result["configs"][cfg] = {"achieved": True, "ir_path": str(out_xml), "nncf_settings": settings}
            print(f"[{model_id}] {cfg}: wrote {out_xml}")
        except Exception as exc:  # noqa: BLE001 -- capability probing must not crash the sweep
            result["configs"][cfg] = {"achieved": False, "reason": f"{type(exc).__name__}: {exc}"}
            print(f"[{model_id}] {cfg}: FAILED -- {type(exc).__name__}: {exc}", file=sys.stderr)

    out_xml = sweep_dir / "int4-weight-only" / f"{model_id}.xml"
    try:
        settings = _quantize_int4_weight_only(fp32_ir, out_xml)
        result["configs"]["int4-weight-only"] = {
            "achieved": True, "ir_path": str(out_xml), "nncf_settings": settings,
        }
        print(f"[{model_id}] int4-weight-only: wrote {out_xml}")
    except Exception as exc:  # noqa: BLE001
        result["configs"]["int4-weight-only"] = {"achieved": False, "reason": f"{type(exc).__name__}: {exc}"}
        print(f"[{model_id}] int4-weight-only: FAILED -- {type(exc).__name__}: {exc}", file=sys.stderr)

    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    evaluable = sorted(mid for mid, spec in REGISTRY.items() if spec.calibration_samples is not None)
    ap.add_argument("--models", default=None,
                     help=f"comma-separated model ids (default: all evaluable). Choices: {evaluable}")
    ap.add_argument("--ir-dir", default=str(common.IR_DIR))
    ap.add_argument("--datasets-dir", default=str(common.DATASETS_DIR))
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else evaluable
    ir_dir = Path(args.ir_dir)
    datasets_dir = Path(args.datasets_dir)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        if REGISTRY[model_id].calibration_samples is None:
            print(f"[{model_id}] no calibration pipeline -- skipping (see quantize_int8.py note)")
            continue

        manifest = build_ladder(model_id, ir_dir, datasets_dir)
        manifest_path = ir_dir / model_id / "quant_sweep" / "manifest.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
        achieved = [c for c, v in manifest["configs"].items() if v["achieved"]]
        print(f"[{model_id}] ladder complete: {achieved} -> {manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
