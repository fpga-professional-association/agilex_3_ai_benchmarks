#!/usr/bin/env python3
"""Adapt MLPerf Tiny ResNet-8 OpenVINO IR to FPGA AI Suite boundaries.

The reference TFLite model accepts INT8 NHWC values whose zero point is -128.
FPGA AI Suite accepts UINT8 input conversion, so the adapted model accepts the
equivalent UINT8 image bytes in NCHW order. The 8x8 average pool is decomposed
into mathematically identical 2x2 and 4x4 stages supported by the architecture.
The 1x1 post-pool NHWC transpose is redundant before flattening and is bypassed.
The final INT8 Convert is removed; the preceding FakeQuantize values are
returned as FP32 and retain the same class ordering and integer-valued scores.
Pass --no-softmax to expose the tensor immediately consumed by the final
Softmax as a deterministic CPU diagnostic output.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

import numpy as np
import openvino as ov


def node_map(model: ov.Model) -> dict[str, ov.Node]:
    return {node.get_friendly_name(): node for node in model.get_ordered_ops()}


def constant_like(port: ov.Output, value: float) -> ov.Output:
    shape = port.get_shape()
    data = np.full(shape, value, dtype=np.float32)
    return ov.opset13.constant(data).output(0)


def replace_global_pool(global_pool: ov.Node) -> ov.Output:
    """Replace 8x8 average pool with exact 2x2 then 4x4 stages."""
    pool_2x2 = ov.opset13.avg_pool(
        global_pool.input_value(0),
        strides=[2, 2],
        pads_begin=[0, 0],
        pads_end=[0, 0],
        kernel_shape=[2, 2],
        exclude_pad=True,
        rounding_type="floor",
        auto_pad="explicit",
    )
    pool_2x2.set_friendly_name("global_avg_pool_2x2")
    pool_4x4 = ov.opset13.avg_pool(
        pool_2x2.output(0),
        strides=[4, 4],
        pads_begin=[0, 0],
        pads_end=[0, 0],
        kernel_shape=[4, 4],
        exclude_pad=True,
        rounding_type="floor",
        auto_pad="explicit",
    )
    pool_4x4.set_friendly_name("global_avg_pool_4x4")
    global_pool.output(0).replace(pool_4x4.output(0))
    return pool_4x4.output(0)


def adapt_quantized(model: ov.Model) -> ov.Model:
    nodes = node_map(model)
    parameter = model.get_parameters()[0]
    input_convert = nodes["Convert_3395"]
    input_fq = nodes["Transpose_50"]
    global_pool = nodes["AvgPool_192"]
    pooled_fq = nodes["Transpose_918"]
    flatten = nodes["13"]
    output_fq = nodes["FakeQuantize_564"]
    result = model.get_results()[0]

    parameter.set_element_type(ov.Type.u8)
    parameter.set_partial_shape(ov.PartialShape([1, 3, 32, 32]))

    # Replace NHWC transpose with a host-side NCHW boundary. UINT8 values
    # 0..255 represent the same pixels as reference INT8 values -128..127.
    input_fq.input(0).replace_source_output(input_convert.output(0))
    input_fq.input(1).replace_source_output(
        constant_like(input_fq.input_value(1), 0.0)
    )
    input_fq.input(2).replace_source_output(
        constant_like(input_fq.input_value(2), 255.0)
    )

    replace_global_pool(global_pool)

    # With 1x1 spatial dimensions, NCHW and NHWC flatten identically.
    flatten.input(0).replace_source_output(pooled_fq.output(0))

    # The FPGA boundary returns the quantized scores as FP32. Argmax and each
    # integer score match the reference INT8 tensor before type conversion.
    result.input(0).replace_source_output(output_fq.output(0))

    model.validate_nodes_and_infer_types()
    parameter.set_friendly_name("image_u8_nchw")
    result.set_friendly_name("scores_i8_as_f32")
    return model


def adapt_float(model: ov.Model) -> ov.Model:
    nodes = node_map(model)
    parameter = model.get_parameters()[0]
    first_convolution = nodes["Convolution_29"]
    global_pool = nodes["AvgPool_125"]
    flatten = nodes["13"]

    # The streaming layout-transform block accepts F16 data. The OpenVINO
    # boundary remains FP32/NCHW so the compiler emits the required transform.
    parameter.set_partial_shape(ov.PartialShape([1, 3, 32, 32]))
    first_convolution.input(0).replace_source_output(parameter.output(0))
    pooled_output = replace_global_pool(global_pool)
    flatten.input(0).replace_source_output(pooled_output)

    model.validate_nodes_and_infer_types()
    parameter.set_friendly_name("image_f32_nchw")
    model.get_results()[0].set_friendly_name("probabilities_f32")
    return model


def expose_pre_softmax(model: ov.Model) -> ov.Model:
    """Replace the result with the tensor consumed by the final Softmax."""
    softmaxes = [
        node for node in model.get_ordered_ops()
        if node.get_type_name().lower() == "softmax"
    ]
    if len(softmaxes) != 1:
        raise RuntimeError(f"expected one final Softmax, found {len(softmaxes)}")
    result = model.get_results()[0]
    result.input(0).replace_source_output(softmaxes[0].input_value(0))
    result.set_friendly_name("logits_pre_softmax")
    model.validate_nodes_and_infer_types()
    return model


def adapt(model: ov.Model, no_softmax: bool = False) -> ov.Model:
    input_type = model.input(0).get_element_type()
    if input_type == ov.Type.i8:
        adapted = adapt_quantized(model)
    elif input_type == ov.Type.f32:
        adapted = adapt_float(model)
    else:
        raise RuntimeError(f"unsupported reference input type: {input_type}")
    return expose_pre_softmax(adapted) if no_softmax else adapted


def synthetic_u8() -> np.ndarray:
    """The same deterministic UINT8 image used by the firmware boundary."""
    state = 0x13579BDF
    image = []
    for index in range(32 * 32 * 3):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        raw_byte = ((state >> 24) ^ (index * 29)) & 0xFF
        image.append(raw_byte ^ 0x80)
    return np.asarray(image, dtype=np.uint8).reshape(1, 32, 32, 3)


def diagnostic_cases() -> list[tuple[str, np.ndarray]]:
    return [
        ("zero", np.zeros((1, 32, 32, 3), dtype=np.uint8)),
        ("synthetic", synthetic_u8()),
    ]


def output_sha256(output: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(output).tobytes()).hexdigest()


def clear_conversion_metadata(model: ov.Model) -> None:
    """Remove source/output paths so serialized XML is location-independent."""
    rt_info = model.get_rt_info()
    for key in list(rt_info):
        del rt_info[key]


def validate_logits(reference_path: Path, adapted_path: Path) -> None:
    """Validate the diagnostic IR and print reproducible pre-Softmax vectors."""
    core = ov.Core()
    reference_model = expose_pre_softmax(core.read_model(str(reference_path)))
    reference = core.compile_model(reference_model, "CPU")
    adapted = core.compile_model(str(adapted_path), "CPU")
    reference_type = reference.input(0).get_element_type()

    for name, image in diagnostic_cases():
        if reference_type == ov.Type.i8:
            reference_input = (image.astype(np.int16) - 128).astype(np.int8)
            adapted_input = np.transpose(image, (0, 3, 1, 2))
        else:
            reference_input = image.astype(np.float32)
            adapted_input = np.transpose(reference_input, (0, 3, 1, 2))
        reference_output = np.asarray(next(iter(reference(reference_input).values())))
        adapted_output = np.asarray(next(iter(adapted(adapted_input).values())))
        max_abs_error = float(np.max(np.abs(reference_output - adapted_output)))
        print(
            f"case={name} reference_sha256={output_sha256(reference_output)} "
            f"adapted_sha256={output_sha256(adapted_output)} "
            f"exact_match={np.array_equal(reference_output, adapted_output)} "
            f"class_match={int(np.argmax(reference_output)) == int(np.argmax(adapted_output))} "
            f"reference_class={int(np.argmax(reference_output))} "
            f"adapted_class={int(np.argmax(adapted_output))} "
            f"max_abs_error={max_abs_error:.9g} "
            f"reference_logits={reference_output.reshape(-1).tolist()} "
            f"adapted_logits={adapted_output.reshape(-1).tolist()}"
        )


def validate(reference_path: Path, adapted_path: Path) -> None:
    core = ov.Core()
    reference = core.compile_model(str(reference_path), "CPU")
    adapted = core.compile_model(str(adapted_path), "CPU")

    rng = np.random.default_rng(20260819)
    cases = [
        np.zeros((1, 32, 32, 3), dtype=np.uint8),
        np.full((1, 32, 32, 3), 255, dtype=np.uint8),
        np.arange(32 * 32 * 3, dtype=np.uint16)
        .reshape(1, 32, 32, 3)
        .astype(np.uint8),
        rng.integers(0, 256, (1, 32, 32, 3), dtype=np.uint8),
    ]

    reference_type = reference.input(0).get_element_type()
    for index, image in enumerate(cases):
        if reference_type == ov.Type.i8:
            reference_input = (image.astype(np.int16) - 128).astype(np.int8)
            adapted_input = np.transpose(image, (0, 3, 1, 2))
        else:
            reference_input = image.astype(np.float32)
            adapted_input = np.transpose(reference_input, (0, 3, 1, 2))
        reference_output = next(iter(reference(reference_input).values()))
        adapted_output = next(iter(adapted(adapted_input).values()))
        if reference_type == ov.Type.i8:
            matches = np.array_equal(
                reference_output, np.rint(adapted_output).astype(np.int8)
            )
            detail = "scores_match=true"
        else:
            max_abs_error = float(
                np.max(np.abs(reference_output - adapted_output))
            )
            matches = (
                int(np.argmax(reference_output)) == int(np.argmax(adapted_output))
                and max_abs_error <= 0.005
            )
            detail = f"class_match=true max_abs_error={max_abs_error:.9g}"
        if not matches:
            raise RuntimeError(
                f"validation case {index} differs: "
                f"reference={reference_output.tolist()} "
                f"adapted={adapted_output.tolist()}"
            )
        print(
            f"case={index} class={int(np.argmax(reference_output))} "
            f"{detail}"
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_model", type=Path)
    parser.add_argument("output_model", type=Path)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument(
        "--no-softmax",
        action="store_true",
        help="Expose the tensor consumed by the final Softmax as the model output",
    )
    args = parser.parse_args()

    core = ov.Core()
    model = adapt(core.read_model(str(args.input_model)), no_softmax=args.no_softmax)
    clear_conversion_metadata(model)
    args.output_model.parent.mkdir(parents=True, exist_ok=True)
    ov.save_model(model, str(args.output_model), compress_to_fp16=False)
    print(f"saved={args.output_model}")

    if args.validate and args.no_softmax:
        validate_logits(args.input_model, args.output_model)
    elif args.validate:
        validate(args.input_model, args.output_model)


if __name__ == "__main__":
    main()
