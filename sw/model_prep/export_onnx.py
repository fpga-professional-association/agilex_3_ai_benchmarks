#!/usr/bin/env python3
"""Export each model's checkpoint to ONNX + a provenance manifest (issue #2).

Writes ``models/onnx/<model_id>.onnx`` and ``models/onnx/<model_id>.manifest.json``. Sanity-checks
the exported param count against PLAN §5's INT8 weight-byte figure (INT8 byte count ~= param
count); a >10% delta is not a hard failure (some models genuinely differ, e.g. ONNX carries
biases/BN params the flash-footprint figure may exclude) but is always recorded in the manifest's
notes, never silently dropped.

    python export_onnx.py                       # all seven, fetching checkpoints first if needed
    python export_onnx.py --models dscnn-kws
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import common
from models import REGISTRY

SANITY_TOLERANCE = 0.10


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--models", default=None,
                     help=f"comma-separated model ids (default: all). Choices: {sorted(REGISTRY)}")
    ap.add_argument("--downloads-dir", default=str(common.DOWNLOADS_DIR))
    ap.add_argument("--onnx-dir", default=str(common.ONNX_DIR))
    args = ap.parse_args(argv)

    model_ids = args.models.split(",") if args.models else sorted(REGISTRY)
    downloads_dir = Path(args.downloads_dir)
    onnx_dir = Path(args.onnx_dir)

    for model_id in model_ids:
        if model_id not in REGISTRY:
            print(f"unknown model id {model_id!r}; choices: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        spec = REGISTRY[model_id]
        print(f"[{model_id}] fetching checkpoint (if needed) ...")
        checkpoint_path = spec.fetch_checkpoint(downloads_dir)

        print(f"[{model_id}] exporting to ONNX ...")
        onnx_path, manifest = spec.export_onnx(checkpoint_path, onnx_dir)

        delta = abs(manifest.param_count - spec.expected_weight_bytes) / spec.expected_weight_bytes
        if delta > SANITY_TOLERANCE:
            note = (
                f"param_count={manifest.param_count} vs PLAN §5 expected~={spec.expected_weight_bytes} "
                f"({delta:.0%} delta, outside +/-{SANITY_TOLERANCE:.0%}). Known cause for models with "
                "many BatchNorm layers: TFLite's converter fuses BatchNorm into the preceding Conv's "
                "weights/bias at graph-optimization time (verified: the raw Keras checkpoint carries "
                "separate BN gamma/beta/mean/var arrays this fused graph no longer has), so the ONNX "
                "param count is the *post-fusion* element count, not the training-time checkpoint's. "
                "PLAN §5's byte figure is the actual reference INT8 .tflite file size (includes "
                "flatbuffer schema + per-tensor quant metadata overhead), not a literal weight-tensor "
                "element count -- the two quantities converge only approximately. See issue #2 step 4."
            )
            manifest.notes = f"{manifest.notes} {note}".strip()
            print(f"[{model_id}] WARNING: {note}")
        else:
            print(f"[{model_id}] param count sanity check OK ({manifest.param_count} vs "
                  f"~{spec.expected_weight_bytes}, {delta:.1%} delta)")

        manifest_path = onnx_path.with_suffix(".manifest.json")
        manifest.write(manifest_path)
        print(f"[{model_id}] wrote {onnx_path} + {manifest_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
