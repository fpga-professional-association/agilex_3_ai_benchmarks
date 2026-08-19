#!/usr/bin/env python3
"""Print a concise OpenVINO graph summary for FPGA AI Suite bring-up."""

from __future__ import annotations

import argparse

import numpy as np
import openvino as ov


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("model")
    parser.add_argument("--fake-quantize-values", action="store_true")
    args = parser.parse_args()

    model = ov.Core().read_model(args.model)
    for index, port in enumerate(model.inputs):
        print(
            f"input[{index}] name={port.get_any_name()} "
            f"type={port.get_element_type()} shape={port.get_partial_shape()}"
        )
    for index, port in enumerate(model.outputs):
        print(
            f"output[{index}] name={port.get_any_name()} "
            f"type={port.get_element_type()} shape={port.get_partial_shape()}"
        )

    for index, node in enumerate(model.get_ordered_ops()):
        inputs = ",".join(str(port.get_element_type()) for port in node.inputs())
        outputs = ",".join(str(port.get_element_type()) for port in node.outputs())
        print(
            f"op[{index:03d}] type={node.get_type_name()} "
            f"name={node.get_friendly_name()} in={inputs} out={outputs}"
        )
        if args.fake_quantize_values and node.get_type_name() == "FakeQuantize":
            values = []
            for input_index in range(1, 5):
                constant = node.input_value(input_index).get_node()
                data = np.asarray(constant.get_data())
                values.append(
                    f"{input_index}:min={data.min():.9g},max={data.max():.9g}"
                )
            print("  fq " + " ".join(values))


if __name__ == "__main__":
    main()
