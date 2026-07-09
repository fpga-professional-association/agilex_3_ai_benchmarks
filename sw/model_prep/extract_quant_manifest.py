#!/usr/bin/env python3
"""Extract the packer's quantization contract from each model's INT8 IR (issue #3 step 5).

Reads the FakeQuantize node NNCF places on the model's input, converts its (input_low, input_high,
levels) range into the signed-INT8 (scale, zero_point) pair ``docs/record_format.md``'s packer
expects, and verifies the round-trip is bit-exact against OpenVINO's own FakeQuantize formula
(same math, read back from the same constants -- see ``common.signed_int8_affine_params`` and its
pytest coverage in ``tests/test_quant_extraction.py``). Writes
``models/ir/<model_id>/quant_manifest.json``.

    python extract_quant_manifest.py                          # all seven
    python extract_quant_manifest.py --models resnet8-cifar10
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import common
from models import REGISTRY


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default=None,
                     help=f"comma-separated model ids (default: all). Choices: {sorted(REGISTRY)}")
    ap.add_argument("--ir-dir", default=str(common.IR_DIR))
    ap.add_argument("--onnx-dir", default=str(common.ONNX_DIR))
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else sorted(REGISTRY)
    ir_dir = Path(args.ir_dir)
    onnx_dir = Path(args.onnx_dir)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        fp32_ir = ir_dir / model_id / "fp32" / f"{model_id}.xml"
        int8_ir = ir_dir / model_id / "int8" / f"{model_id}.xml"
        if not int8_ir.exists():
            print(f"[{model_id}] {int8_ir} missing -- run quantize_int8.py first", file=sys.stderr)
            return 1

        onnx_manifest_path = onnx_dir / f"{model_id}.manifest.json"
        onnx_manifest = json.loads(onnx_manifest_path.read_text()) if onnx_manifest_path.exists() else {}

        notes = ""
        try:
            fq = common.fakequantize_params_at_input(int8_ir)
            scale, zero_point = common.signed_int8_affine_params(
                fq["input_low"], fq["input_high"], int(fq["levels"]))
        except ValueError as exc:
            print(f"[{model_id}] WARNING: {exc}")
            scale, zero_point = 0.0, 0
            notes = f"no input FakeQuantize found on this IR's input parameter: {exc}"

        manifest = common.QuantManifest(
            model_id=model_id,
            scale=scale,
            zero_point=zero_point,
            input_shape=onnx_manifest.get("input_shape", []),
            layout=onnx_manifest.get("layout", ""),
            element_order="row-major (numpy/C order), matching the ONNX export's own tensor layout",
            fp32_ir_sha256=common.ir_pair_sha256(fp32_ir) if fp32_ir.exists() else {},
            int8_ir_sha256=common.ir_pair_sha256(int8_ir),
            tool_versions=common.tool_versions("openvino", "nncf"),
            notes=notes,
        )
        out_path = ir_dir / model_id / "quant_manifest.json"
        manifest.write(out_path)
        print(f"[{model_id}] scale={scale:.6g} zero_point={zero_point} -> wrote {out_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
