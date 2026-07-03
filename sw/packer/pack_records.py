#!/usr/bin/env python3
"""Pack a dataset into a HyperRAM record image (issue #5).

Reads INT8-or-float input tensors + golden labels, quantizes with the exact IR scale/zero-point,
serializes each sample in the chosen engine layout, and writes `<name>.recimg` plus a sidecar
`<name>.recimg.manifest.json` (docs/record_format.md). Refuses to emit an image that would overflow
the record store (16 MB − log reserve − resident weights).

Inputs come from an .npz with arrays `inputs` (N, …) and `labels` (N,). The quantization manifest is
JSON: at minimum {"scale": float, "zero_point": int}; optional model_id, ir_hash, dataset, split,
layout, input_shape.

    python pack_records.py --inputs data.npz --quant-manifest q.json --layout raw --out kws.recimg

Importable: `pack(...)` returns (image_bytes, manifest_dict) with no file I/O, for tests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
from pathlib import Path

import numpy as np

import layouts
import packlib


def pack(inputs: np.ndarray, labels: np.ndarray, scale: float, zero_point: int,
         layout: str = "raw", *, limit: int | None = None, seed: int | None = None,
         reserve_weights: int = 0, hr_bytes: int = packlib.HR_BYTES,
         log_reserve: int = packlib.LOG_RESERVE, model_id: str = "", ir_hash: str = "",
         dataset: str = "", split: str = "") -> tuple[bytes, dict]:
    inputs = np.asarray(inputs)
    labels = np.asarray(labels).astype(np.int64)
    if inputs.shape[0] != labels.shape[0]:
        raise ValueError(f"inputs ({inputs.shape[0]}) and labels ({labels.shape[0]}) length mismatch")
    total = inputs.shape[0]

    # deterministic subset selection
    if limit is not None and limit < total:
        if seed is not None:
            idx = np.random.default_rng(seed).permutation(total)[:limit]
            idx.sort()   # keep records in ascending source order for reproducible, inspectable images
        else:
            idx = np.arange(limit)
        inputs, labels = inputs[idx], labels[idx]
    count = inputs.shape[0]
    if count == 0:
        raise ValueError("no records to pack")

    quantized = inputs.dtype != np.int8
    n_bytes = None
    stride = None
    records = bytearray()
    for i in range(count):
        q = packlib.quantize_int8(inputs[i], scale, zero_point)
        body = layouts.transform(layout, q)
        if n_bytes is None:
            n_bytes = len(body)
            stride = packlib.stride_for(n_bytes)
            fits = packlib.max_records(stride, hr_bytes, log_reserve, reserve_weights)
            if count > fits:
                raise ValueError(
                    f"{count} records of stride {stride} B exceed the record store; "
                    f"max that fits = {fits} (avail "
                    f"{packlib.available_bytes(hr_bytes, log_reserve, reserve_weights)} B)")
        elif len(body) != n_bytes:
            raise ValueError(f"record {i} has {len(body)} bytes, expected {n_bytes} (ragged inputs)")
        lab = int(labels[i])
        if not (0 <= lab <= 255):
            raise ValueError(f"label {lab} at record {i} out of byte range")
        records += packlib.build_record(body, lab, stride)

    image = bytes(records)
    manifest = {
        "model_id": model_id,
        "ir_hash": ir_hash,
        "dataset": dataset,
        "split": split,
        "layout": layout,
        "quantized": bool(quantized),
        "scale": float(scale),
        "zero_point": int(zero_point),
        "input_shape": list(inputs.shape[1:]),
        "n_input_bytes": int(n_bytes),
        "stride": int(stride),
        "record_count": int(count),
        "image_bytes": len(image),
        "reserve_weights": int(reserve_weights),
        "log_reserve": int(log_reserve),
        "hr_bytes": int(hr_bytes),
        "sha256": hashlib.sha256(image).hexdigest(),
        "tool_versions": {"python": platform.python_version(), "numpy": np.__version__},
    }
    return image, manifest


def _load_npz(path: Path):
    with np.load(path) as z:
        if "inputs" not in z or "labels" not in z:
            raise KeyError(f"{path} must contain arrays 'inputs' and 'labels'")
        return z["inputs"], z["labels"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--inputs", required=True, help=".npz with arrays 'inputs' and 'labels'")
    ap.add_argument("--quant-manifest", required=True, help="JSON with scale/zero_point (+ metadata)")
    ap.add_argument("--out", required=True, help="output .recimg path")
    ap.add_argument("--layout", default=None, help=f"one of {sorted(layouts.LAYOUTS)} (default: manifest or raw)")
    ap.add_argument("--limit", type=int, default=None, help="pack at most N records")
    ap.add_argument("--seed", type=int, default=None, help="random subset seed (deterministic)")
    ap.add_argument("--reserve-weights", type=int, default=0, help="bytes of HyperRAM-resident weights")
    args = ap.parse_args(argv)

    qm = json.loads(Path(args.quant_manifest).read_text())
    if "scale" not in qm or "zero_point" not in qm:
        print("quant-manifest must define 'scale' and 'zero_point'", file=sys.stderr)
        return 2
    layout = args.layout or qm.get("layout", "raw")
    inputs, labels = _load_npz(Path(args.inputs))

    try:
        image, manifest = pack(
            inputs, labels, qm["scale"], qm["zero_point"], layout,
            limit=args.limit, seed=args.seed, reserve_weights=args.reserve_weights,
            model_id=qm.get("model_id", ""), ir_hash=qm.get("ir_hash", ""),
            dataset=qm.get("dataset", ""), split=qm.get("split", ""))
    except ValueError as exc:
        print(f"pack failed: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.write_bytes(image)
    Path(str(out) + ".manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"wrote {out} ({manifest['record_count']} records, stride {manifest['stride']} B, "
          f"{manifest['image_bytes']} B, sha256 {manifest['sha256'][:16]}…)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
