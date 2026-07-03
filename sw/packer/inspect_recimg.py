#!/usr/bin/env python3
"""Inspect / verify a HyperRAM record image (issue #5).

Checks an image against its manifest: file size, stride is a 64-byte multiple, SHA-256 match, every
record's pad bytes are zero, the record store stays below the log reserve, and prints a label
histogram. Can decode any record back to its stored INT8 tensor bytes for round-trip checks.

    python inspect_recimg.py kws.recimg --check
    python inspect_recimg.py kws.recimg --decode 0
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

import packlib


def load(image_path: Path):
    image = image_path.read_bytes()
    manifest = json.loads(Path(str(image_path) + ".manifest.json").read_text())
    return image, manifest


def verify(image: bytes, manifest: dict) -> list[str]:
    """Return a list of problems (empty == the image is consistent with its manifest)."""
    errs: list[str] = []
    stride = manifest["stride"]
    n = manifest["n_input_bytes"]
    count = manifest["record_count"]

    if stride % packlib.BURST_ALIGN != 0:
        errs.append(f"stride {stride} is not a multiple of {packlib.BURST_ALIGN}")
    if stride != packlib.stride_for(n):
        errs.append(f"stride {stride} != stride_for({n}) = {packlib.stride_for(n)}")
    if len(image) != stride * count:
        errs.append(f"image is {len(image)} B, expected stride*count = {stride*count}")
    if manifest.get("image_bytes") != len(image):
        errs.append(f"manifest image_bytes {manifest.get('image_bytes')} != actual {len(image)}")
    if hashlib.sha256(image).hexdigest() != manifest["sha256"]:
        errs.append("SHA-256 mismatch between image and manifest")

    # record store must stay below the log reserve (+ any resident weights)
    avail = packlib.available_bytes(manifest.get("hr_bytes", packlib.HR_BYTES),
                                    manifest.get("log_reserve", packlib.LOG_RESERVE),
                                    manifest.get("reserve_weights", 0))
    if len(image) > avail:
        errs.append(f"image {len(image)} B exceeds available record store {avail} B (log reserve!)")

    # pad bytes (between label+1 and stride) must be zero in every record
    for k in range(count):
        rec = image[k * stride:(k + 1) * stride]
        if any(rec[n + 1:]):
            errs.append(f"record {k} has non-zero pad bytes")
            break
    return errs


def decode(image: bytes, manifest: dict, k: int) -> tuple[np.ndarray, int]:
    """Return (int8 tensor bytes as stored, label) for record k."""
    stride = manifest["stride"]
    n = manifest["n_input_bytes"]
    if not (0 <= k < manifest["record_count"]):
        raise IndexError(f"record {k} out of range 0..{manifest['record_count']-1}")
    rec = image[k * stride:(k + 1) * stride]
    tensor = np.frombuffer(rec[:n], dtype=np.int8).copy()
    label = rec[n]
    return tensor, label


def label_histogram(image: bytes, manifest: dict) -> dict[int, int]:
    stride, n, count = manifest["stride"], manifest["n_input_bytes"], manifest["record_count"]
    hist: dict[int, int] = {}
    for k in range(count):
        lab = image[k * stride + n]
        hist[lab] = hist.get(lab, 0) + 1
    return dict(sorted(hist.items()))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("image", help="path to the .recimg file")
    ap.add_argument("--check", action="store_true", help="verify and exit nonzero on any problem")
    ap.add_argument("--decode", type=int, metavar="K", help="print record K's tensor bytes + label")
    ap.add_argument("--hist", action="store_true", help="print the label histogram")
    args = ap.parse_args(argv)

    image, manifest = load(Path(args.image))
    print(f"{args.image}: {manifest['record_count']} records, stride {manifest['stride']} B, "
          f"N={manifest['n_input_bytes']}, layout={manifest['layout']}, "
          f"dataset={manifest.get('dataset','?')}")

    if args.decode is not None:
        tensor, label = decode(image, manifest, args.decode)
        print(f"record {args.decode}: label={label}, first bytes={tensor[:16].tolist()}")

    if args.hist:
        print("label histogram:", label_histogram(image, manifest))

    if args.check:
        errs = verify(image, manifest)
        if errs:
            for e in errs:
                print(f"FAIL: {e}", file=sys.stderr)
            return 1
        print("OK: image consistent with manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
