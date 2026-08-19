#!/usr/bin/env python3
"""Convert a pinned TFLite model to a deterministic OpenVINO IR pair."""

from __future__ import annotations

import argparse
from pathlib import Path

import openvino as ov


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_model", type=Path)
    parser.add_argument("output_model", type=Path)
    args = parser.parse_args()

    if args.input_model.suffix.lower() != ".tflite":
        raise RuntimeError("input must be a .tflite model")
    if args.output_model.suffix.lower() != ".xml":
        raise RuntimeError("output must be an OpenVINO .xml path")

    args.output_model.parent.mkdir(parents=True, exist_ok=True)
    # The conversion API assigns the stable frontend operation names used by
    # the graph-adaptation step. Core.read_model() loads TFLite too, but its
    # friendly names differ in OpenVINO 2025.4.
    model = ov.convert_model(str(args.input_model))
    ov.save_model(model, str(args.output_model), compress_to_fp16=False)
    print(f"saved={args.output_model}")


if __name__ == "__main__":
    main()
