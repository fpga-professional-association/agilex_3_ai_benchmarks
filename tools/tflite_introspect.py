#!/usr/bin/env python3
"""Small, read-only TFLite FlatBuffer inspector.

This intentionally implements only the FlatBuffer primitives needed by a
TFLite Model.  It has no TensorFlow, FlatBuffers, NumPy, or other dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import struct
import sys
from pathlib import Path


class ParseError(ValueError):
    """Raised for an invalid FlatBuffer or an out-of-bounds table offset."""


class FB:
    def __init__(self, data: bytes):
        self.data = data
        self.n = len(data)

    def need(self, pos: int, size: int, what: str = "data") -> None:
        if pos < 0 or size < 0 or pos > self.n - size:
            raise ParseError(f"{what} out of bounds at {pos} (+{size})")

    def u8(self, pos: int) -> int:
        self.need(pos, 1)
        return self.data[pos]

    def i8(self, pos: int) -> int:
        self.need(pos, 1)
        return struct.unpack_from("<b", self.data, pos)[0]

    def u16(self, pos: int) -> int:
        self.need(pos, 2)
        return struct.unpack_from("<H", self.data, pos)[0]

    def i32(self, pos: int) -> int:
        self.need(pos, 4)
        return struct.unpack_from("<i", self.data, pos)[0]

    def u32(self, pos: int) -> int:
        self.need(pos, 4)
        return struct.unpack_from("<I", self.data, pos)[0]

    def i64(self, pos: int) -> int:
        self.need(pos, 8)
        return struct.unpack_from("<q", self.data, pos)[0]

    def f32(self, pos: int) -> float:
        self.need(pos, 4)
        return struct.unpack_from("<f", self.data, pos)[0]

    def root(self) -> int:
        if self.n < 8 or self.data[4:8] != b"TFL3":
            raise ParseError("missing TFL3 identifier")
        rel = self.u32(0)
        self.need(rel, 4, "root table")
        return rel

    def table(self, pos: int) -> tuple[int, int, int]:
        self.need(pos, 4, "table")
        back = self.i32(pos)
        vtable = pos - back
        self.need(vtable, 4, "vtable")
        vlen = self.u16(vtable)
        objlen = self.u16(vtable + 2)
        if vlen < 4 or vlen % 2 or vtable + vlen > self.n:
            raise ParseError(f"invalid vtable length {vlen} at {vtable}")
        if objlen < 4 or pos + objlen > self.n:
            raise ParseError(f"invalid table length {objlen} at {pos}")
        # A vtable must include only 16-bit field entries and must not point
        # into an impossible negative region.
        if vtable + vlen > self.n:
            raise ParseError("invalid vtable location")
        return vtable, vlen, objlen

    def field(self, table: int, index: int) -> int | None:
        vtable, vlen, _ = self.table(table)
        p = vtable + 4 + index * 2
        if p + 2 > vtable + vlen:
            return None
        off = self.u16(p)
        if off == 0:
            return None
        if off >= self.u16(vtable + 2):
            raise ParseError(f"field {index} outside table at {table}")
        self.need(table + off, 1, "field")
        return table + off

    def scalar(self, table: int, index: int, kind: str, default=0):
        p = self.field(table, index)
        if p is None:
            return default
        widths = {"u8": 1, "i8": 1, "u16": 2, "i32": 4, "u32": 4,
                  "i64": 8, "f32": 4}
        if kind not in widths:
            raise ParseError(f"unsupported scalar kind {kind}")
        _, _, objlen = self.table(table)
        width = widths[kind]
        if p > table + objlen - width:
            raise ParseError(f"scalar {kind} at {p} exceeds table object at {table}")
        return {"u8": self.u8, "i8": self.i8, "u16": self.u16,
                "i32": self.i32, "u32": self.u32, "i64": self.i64,
                "f32": self.f32}[kind](p)

    def target(self, field_pos: int, what: str = "uoffset") -> int:
        rel = self.u32(field_pos)
        if rel == 0:
            raise ParseError(f"null {what}")
        target = field_pos + rel
        self.need(target, 4, what)
        return target

    def string(self, table: int, index: int, default: str | None = None):
        p = self.field(table, index)
        if p is None:
            return default
        s = self.target(p, "string")
        length = self.u32(s)
        self.need(s + 4, length, "string bytes")
        raw = self.data[s + 4:s + 4 + length]
        # The terminator is part of a FlatBuffer string's storage.
        self.need(s + 4 + length, 1, "string terminator")
        if self.data[s + 4 + length] != 0:
            raise ParseError("unterminated FlatBuffer string")
        return raw.decode("utf-8", errors="replace")

    def vector_info(self, table: int, index: int) -> tuple[int, int] | None:
        p = self.field(table, index)
        if p is None:
            return None
        v = self.target(p, "vector")
        count = self.u32(v)
        self.need(v + 4, 0, "vector")
        return v + 4, count

    def vector_scalars(self, table: int, index: int, kind: str) -> list:
        info = self.vector_info(table, index)
        if info is None:
            return []
        start, count = info
        sizes = {"u8": 1, "i8": 1, "u16": 2, "i32": 4, "u32": 4,
                 "i64": 8, "f32": 4}
        if kind not in sizes:
            raise ParseError(f"unsupported vector kind {kind}")
        size = sizes[kind]
        self.need(start, count * size, "vector elements")
        read = {"u8": self.u8, "i8": self.i8, "u16": self.u16,
                "i32": self.i32, "u32": self.u32, "i64": self.i64,
                "f32": self.f32}[kind]
        return [read(start + i * size) for i in range(count)]

    def vector_tables(self, table: int, index: int) -> list[int]:
        info = self.vector_info(table, index)
        if info is None:
            return []
        start, count = info
        self.need(start, count * 4, "table vector elements")
        out = []
        for i in range(count):
            p = start + i * 4
            out.append(self.target(p, "table element"))
        return out

    def vector_bytes(self, table: int, index: int) -> tuple[int, bytes] | None:
        info = self.vector_info(table, index)
        if info is None:
            return None
        start, count = info
        self.need(start, count, "byte vector")
        return start, self.data[start:start + count]


# Stable names for the operators occurring in common TFLite models. Unknown
# values remain explicit as BUILTIN_<number>, keeping the manifest lossless.
BUILTINS = {
    0: "ADD", 1: "AVERAGE_POOL_2D", 2: "CONCATENATION", 3: "CONV_2D",
    4: "DEPTHWISE_CONV_2D", 5: "DEPTH_TO_SPACE", 6: "DEQUANTIZE",
    7: "EMBEDDING_LOOKUP", 8: "FLOOR", 9: "FULLY_CONNECTED", 10: "HASHTABLE_LOOKUP",
    11: "L2_NORMALIZATION", 12: "L2_POOL_2D", 13: "LOCAL_RESPONSE_NORMALIZATION",
    14: "LOGISTIC", 15: "LSH_PROJECTION", 16: "LSTM", 17: "MAX_POOL_2D",
    18: "MUL", 19: "RELU", 20: "RELU_N1_TO_1", 21: "RELU6", 22: "RESHAPE",
    23: "RESIZE_BILINEAR", 24: "RNN", 25: "SOFTMAX", 26: "SPACE_TO_DEPTH",
    27: "SVDF", 28: "TANH", 29: "CONV_3D", 30: "HASHTABLE", 31: "HASHTABLE_FIND",
    32: "HASHTABLE_IMPORT", 33: "HASHTABLE_SIZE", 34: "IF", 35: "WHILE",
    36: "SPLIT", 37: "SPLIT_V", 38: "STRIDED_SLICE", 39: "BIDIRECTIONAL_SEQUENCE_RNN",
    40: "ABS", 41: "ADD_N", 42: "ARG_MAX", 43: "ARG_MIN", 44: "BATCH_TO_SPACE_ND",
    45: "BATCH_MATMUL", 46: "CAST", 47: "DIV", 48: "EMBEDDING_LOOKUP_SPARSE",
    49: "EXP", 50: "FLOOR_DIV", 51: "FLOOR_MOD", 52: "GATHER", 53: "GATHER_ND",
    54: "GELU", 55: "GREATER", 56: "GREATER_EQUAL", 57: "LESS", 58: "LESS_EQUAL",
    59: "LESS_EQUAL", 60: "LOG", 61: "LOGICAL_AND", 62: "LOGICAL_NOT",
    63: "LOGICAL_OR", 64: "MAXIMUM", 65: "MINIMUM", 66: "MIRROR_PAD", 67: "MATRIX_DIAG",
    68: "MATRIX_SET_DIAG", 69: "PACK", 70: "PAD", 71: "PADV2", 72: "RANK",
    73: "REDUCE_ALL", 74: "REDUCE_ANY", 75: "REDUCE_MAX", 76: "REDUCE_MIN",
    77: "REDUCE_PROD", 78: "REDUCE_SUM", 79: "REVERSE_SEQUENCE", 80: "SCATTER_ND",
    81: "SELECT", 82: "SIN", 83: "SLICE", 84: "SQUEEZE", 85: "SPACE_TO_BATCH_ND",
    86: "SPLIT_V", 87: "SQRT", 88: "SQUARE", 89: "ZEROS_LIKE", 90: "FILL",
    91: "FLOOR_MOD", 92: "RANGE", 93: "RESIZE_NEAREST_NEIGHBOR", 94: "LEAKY_RELU",
    95: "SQUARED_DIFFERENCE", 96: "ONE_HOT", 97: "LOG_SOFTMAX", 98: "DELEGATE",
    99: "FAKE_QUANT", 100: "REDUCE_LOGSUMEXP", 101: "ABS", 102: "COS", 103: "RFFT2D",
    104: "CONV_3D", 105: "IMAG", 106: "REAL", 107: "COMPLEX_ABS", 108: "HASHTABLE_FIND",
    109: "HASHTABLE_IMPORT", 110: "HASHTABLE_SIZE", 111: "LOGICAL_XOR", 112: "MATRIX_BAND_PART",
    113: "DYNAMIC_UPDATE_SLICE", 114: "UNPACK", 115: "HARD_SWISH", 116: "IF",
    117: "WHILE", 118: "NON_MAX_SUPPRESSION_V4", 119: "NON_MAX_SUPPRESSION_V5",
    120: "SCATTER_ND", 121: "TRANSPOSE_CONV", 122: "BATCH_MATMUL", 123: "GELU",
    124: "DYNAMIC_QUANTIZE", 125: "REVERSE_V2", 126: "ADD", 127: "SUB", 128: "DIV",
    129: "MUL", 130: "UNIDIRECTIONAL_SEQUENCE_LSTM", 131: "GATHER", 132: "DENSIFY",
    133: "SEGMENT_SUM", 134: "BATCHED_GATHER", 135: "CUMSUM", 136: "CALL_ONCE",
    137: "BROADCAST_TO", 138: "RANDOM_STANDARD_NORMAL", 139: "BUCKETIZE", 140: "GELU",
    141: "POW", 142: "ELU", 143: "RELU_0_TO_1", 144: "SIGN", 145: "RELU1",
}
# Canonical TFLite names (the legacy entries above are retained only as a
# fallback for values beyond this table).  The model's schema revision assigns
# QUANTIZE to 114; keep that value explicit rather than mislabelling it as an
# unrelated newer operator.
BUILTINS.update({
    0: "ADD", 1: "AVERAGE_POOL_2D", 2: "CONCATENATION", 3: "CONV_2D",
    4: "DEPTHWISE_CONV_2D", 5: "DEPTH_TO_SPACE", 6: "DEQUANTIZE",
    7: "EMBEDDING_LOOKUP", 8: "FLOOR", 9: "FULLY_CONNECTED",
    10: "HASHTABLE_LOOKUP", 11: "L2_NORMALIZATION", 12: "L2_POOL_2D",
    13: "LOCAL_RESPONSE_NORMALIZATION", 14: "LOGISTIC", 15: "LSH_PROJECTION",
    16: "LSTM", 17: "MAX_POOL_2D", 18: "MUL", 19: "RELU",
    20: "RELU_N1_TO_1", 21: "RELU6", 22: "RESHAPE", 23: "RESIZE_BILINEAR",
    24: "RNN", 25: "SOFTMAX", 26: "SPACE_TO_DEPTH", 27: "SVDF",
    28: "TANH", 29: "CONCAT_EMBEDDINGS", 30: "SKIP_GRAM", 31: "CALL",
    32: "CUSTOM", 33: "EMBEDDING_LOOKUP_SPARSE", 34: "PAD", 35: "GATHER",
    36: "BATCH_TO_SPACE_ND", 37: "SPACE_TO_BATCH_ND", 38: "TRANSPOSE",
    39: "MEAN", 40: "SUB", 41: "DIV", 42: "SQUEEZE",
    43: "UNIDIRECTIONAL_SEQUENCE_RNN", 44: "EXP", 45: "TOPK_V2",
    46: "SPLIT", 47: "LOG_SOFTMAX", 48: "DELEGATE", 49: "BIDIRECTIONAL_SEQUENCE_RNN",
    50: "CAST", 51: "PRELU", 52: "MAXIMUM", 53: "ARG_MAX", 54: "MINIMUM",
    55: "LESS", 56: "NEG", 57: "PADV2", 58: "GREATER", 59: "GREATER_EQUAL",
    60: "LESS_EQUAL", 61: "SELECT", 62: "SLICE", 63: "SIN", 64: "TRANSPOSE_CONV",
    65: "SPARSE_TO_DENSE", 66: "TILE", 67: "EXPAND_DIMS", 68: "EQUAL",
    69: "NOT_EQUAL", 70: "LOGICAL_OR", 71: "FLOOR_DIV", 72: "REDUCE_ANY",
    73: "SQUARE", 74: "ZEROS_LIKE", 75: "FILL", 76: "FLOOR_MOD", 77: "RANGE",
    78: "RESIZE_NEAREST_NEIGHBOR", 79: "LEAKY_RELU", 80: "SQUARED_DIFFERENCE",
    81: "MIRROR_PAD", 82: "ABS", 83: "SPLIT_V", 84: "UNIQUE", 85: "CEIL",
    86: "REVERSE_V2", 87: "ADD_N", 88: "GATHER_ND", 89: "COS", 90: "WHERE",
    91: "RANK", 92: "ELU", 93: "REVERSE_SEQUENCE", 94: "MATRIX_DIAG",
    95: "QUANTIZE", 96: "MATRIX_SET_DIAG", 97: "ROUND", 98: "HARD_SWISH",
    99: "IF", 100: "WHILE", 101: "NON_MAX_SUPPRESSION_V4",
    102: "NON_MAX_SUPPRESSION_V5", 103: "SCATTER_ND", 104: "SELECT_V2",
    105: "DENSIFY", 106: "SEGMENT_SUM", 107: "BATCHED_GATHER", 108: "CUMSUM",
    109: "CALL_ONCE", 110: "BROADCAST_TO", 111: "RFFT2D", 112: "CONV_3D",
    113: "IMAG", 114: "QUANTIZE", 115: "HASHTABLE", 116: "HASHTABLE_FIND",
    117: "HASHTABLE_IMPORT", 118: "HASHTABLE_SIZE", 119: "REVERSE",
    120: "STABLEHLO_COMPOSITE", 121: "STABLEHLO_SCATTER",
    122: "STABLEHLO_GATHER", 123: "STABLEHLO_REDUCE",
    124: "STABLEHLO_RNG", 125: "STABLEHLO_CONVOLUTION",
    126: "STABLEHLO_BROADCAST_IN_DIM", 127: "STABLEHLO_PAD",
})
TYPES = {0: "FLOAT32", 1: "FLOAT16", 2: "INT32", 3: "UINT8", 4: "INT64",
         5: "STRING", 6: "BOOL", 7: "INT16", 8: "COMPLEX64", 9: "INT8",
         10: "FLOAT64", 11: "COMPLEX128", 12: "UINT64", 13: "RESOURCE",
         14: "VARIANT", 15: "UINT32", 16: "UINT16", 17: "INT4", 18: "BFLOAT16"}

PADDING = {0: "SAME", 1: "VALID"}
ACTIVATION = {0: "NONE", 1: "RELU", 2: "RELU_N1_TO_1", 3: "RELU6",
              4: "TANH", 5: "SIGN_BIT"}
WEIGHTS_FORMAT = {0: "DEFAULT", 1: "SHUFFLED4x16", 2: "SHUFFLED16x1"}
BUILTIN_OPTIONS = {
    0: "NONE", 1: "Conv2DOptions", 5: "Pool2DOptions", 8: "FullyConnectedOptions",
    9: "SoftmaxOptions", 11: "AddOptions",
}
TYPE_WIDTH = {0: 4, 1: 2, 2: 4, 3: 1, 4: 8, 6: 1, 7: 2, 9: 1,
              10: 8, 12: 8, 15: 4, 16: 2, 17: 1, 18: 2}


def enum_value(value: int, names: dict[int, str], prefix: str) -> dict:
    return {"code": value, "name": names.get(value, f"{prefix}_{value}")}


def option_table(fb: FB, op: int, option_type: int) -> int | None:
    """Resolve an Operator builtin-options union with strict FlatBuffer checks."""
    field = fb.field(op, 4)
    _, _, objlen = fb.table(op)
    if field is not None and field > op + objlen - 4:
        raise ParseError("builtin options union offset exceeds operator object")
    if option_type == 0:
        if field is not None:
            # A NONE union must have a null table offset, not a dangling one.
            if fb.scalar(op, 4, "u32", 0) != 0:
                raise ParseError("NONE builtin options has a non-null table")
        return None
    if field is None:
        raise ParseError(f"builtin options type {option_type} has no table")
    target = fb.target(field, "builtin options table")
    fb.table(target)
    return target


def decode_options(fb: FB, op: int, operator: str, tensors: list[dict], buffers: list[dict]) -> tuple[dict, dict]:
    option_type = fb.scalar(op, 3, "u8", 0)
    option_name = BUILTIN_OPTIONS.get(option_type, f"BUILTIN_OPTIONS_{option_type}")
    table = option_table(fb, op, option_type)
    result: dict = {"type": option_name, "table_present": table is not None}
    if operator == "CONV_2D":
        if table is None or option_type != 1:
            raise ParseError("CONV_2D has invalid builtin options union")
        result.update({"padding": enum_value(fb.scalar(table, 0, "u8", 0), PADDING, "PADDING"),
                       "stride_w": fb.scalar(table, 1, "u32", 1),
                       "stride_h": fb.scalar(table, 2, "u32", 1),
                       "fused_activation": enum_value(fb.scalar(table, 3, "u8", 0), ACTIVATION, "ACTIVATION"),
                       "dilation_w_factor": fb.scalar(table, 4, "u32", 1),
                       "dilation_h_factor": fb.scalar(table, 5, "u32", 1)})
    elif operator == "ADD":
        if table is None or option_type != 11:
            raise ParseError("ADD has invalid builtin options union")
        result["fused_activation"] = enum_value(fb.scalar(table, 0, "u8", 0), ACTIVATION, "ACTIVATION")
    elif operator == "AVERAGE_POOL_2D":
        if table is None or option_type != 5:
            raise ParseError("AVERAGE_POOL_2D has invalid builtin options union")
        result.update({"padding": enum_value(fb.scalar(table, 0, "u8", 0), PADDING, "PADDING"),
                       "stride_w": fb.scalar(table, 1, "u32", 1),
                       "stride_h": fb.scalar(table, 2, "u32", 1),
                       "filter_width": fb.scalar(table, 3, "u32", 0),
                       "filter_height": fb.scalar(table, 4, "u32", 0),
                       "fused_activation": enum_value(fb.scalar(table, 5, "u8", 0), ACTIVATION, "ACTIVATION")})
    elif operator == "FULLY_CONNECTED":
        if table is None or option_type != 8:
            raise ParseError("FULLY_CONNECTED has invalid builtin options union")
        result.update({"fused_activation": enum_value(fb.scalar(table, 0, "u8", 0), ACTIVATION, "ACTIVATION"),
                       "weights_format": enum_value(fb.scalar(table, 1, "u8", 0), WEIGHTS_FORMAT, "WEIGHTS_FORMAT"),
                       "keep_num_dims": bool(fb.scalar(table, 2, "u8", 0)),
                       "asymmetric_quantize_inputs": bool(fb.scalar(table, 3, "u8", 0)),
                       "quantized_bias_type": enum_value(fb.scalar(table, 4, "u8", 2), TYPES, "TYPE")})
    elif operator == "SOFTMAX":
        if table is None or option_type != 9:
            raise ParseError("SOFTMAX has invalid builtin options union")
        result["beta"] = fb.scalar(table, 0, "f32", 1.0)
    elif operator == "RESHAPE":
        # This model uses the modern no-options form; its shape is the int32
        # constant supplied as input 1.  Decode that constant for static use.
        if option_type not in (0, 17):
            raise ParseError(f"RESHAPE has invalid builtin options type {option_type}")
        result["new_shape"] = None
        result["new_shape_source"] = "operator input"
        if table is not None:
            result["new_shape"] = fb.vector_scalars(table, 0, "i32")
            result["new_shape_source"] = "ReshapeOptions.new_shape"
        else:
            input_indices = fb.vector_scalars(op, 1, "i32")
            shape_index = input_indices[1] if len(input_indices) > 1 else -1
            if not (0 <= shape_index < len(tensors)):
                return {"code": option_type, "name": option_name}, result
            shape_tensor = tensors[shape_index]
            bi = shape_tensor["buffer"]
            b = buffers[bi] if 0 <= bi < len(buffers) else None
            if b and b["size"] and shape_tensor["type_code"] == 2:
                fb.need(b["data_offset"], b["size"], "reshape constant")
                if b["size"] % 4:
                    raise ParseError("reshape int32 constant has non-word size")
                result["new_shape"] = [fb.i32(b["data_offset"] + j)
                                        for j in range(0, b["size"], 4)]
                result["new_shape_source"] = f"constant tensor {shape_tensor['index']}"
    return {"code": option_type, "name": option_name}, result


def tensor_raw_size(tensor: dict) -> int | None:
    width = TYPE_WIDTH.get(tensor["type_code"])
    if width is None or any(dim < 0 for dim in tensor["shape"]):
        return None
    size = width
    for dim in tensor["shape"]:
        size *= dim
    return size


def memory_summary(subgraphs: list[dict], buffers: list[dict]) -> dict:
    constant_bytes = sum(b["size"] for b in buffers if b["size"])
    candidates = []
    for sg in subgraphs:
        for t in sg["tensors"]:
            # Empty buffers are tensor arena values (input/intermediate/output)
            # rather than embedded constants.  This is only a sizing proxy.
            bi = t["buffer"]
            if 0 <= bi < len(buffers) and buffers[bi]["size"] == 0:
                raw = tensor_raw_size(t)
                if raw is not None:
                    candidates.append((raw, t, sg["index"]))
    largest = None
    if candidates:
        raw, t, sg_i = max(candidates, key=lambda item: (item[0], -item[1]["index"]))
        largest = {"subgraph": sg_i, "tensor": t["index"], "name": t["name"],
                   "shape": t["shape"], "type": t["type"], "raw_size_bytes": raw}
    return {"total_constant_bytes": constant_bytes,
            "largest_live_activation_tensor": largest,
            "largest_live_activation_note":
                "Approximation based on the non-constant activation arena; this is not a full liveness plan."}


def op_name(code: int, custom: str | None) -> str:
    if code == 32 and custom:
        return custom
    return BUILTINS.get(code, f"BUILTIN_{code}")


def parse_model(data: bytes, source_name: str = "") -> dict:
    fb = FB(data)
    root = fb.root()
    version = fb.scalar(root, 0, "u32", 0)
    op_tables = fb.vector_tables(root, 1)
    ops = []
    for i, t in enumerate(op_tables):
        # builtin_code was added after deprecated_builtin_code.  For old
        # models, field 3 is absent and field 0 carries the value.
        deprecated = fb.scalar(t, 0, "i8", 0)
        custom = fb.string(t, 1)
        code = fb.scalar(t, 3, "i32", deprecated)
        ops.append({"index": i, "builtin_code": code,
                    "builtin": op_name(code, custom), "custom_code": custom,
                    "version": fb.scalar(t, 2, "i32", 1)})

    buffers = []
    for i, t in enumerate(fb.vector_tables(root, 4)):
        vec = fb.vector_bytes(t, 0)
        if vec is None:
            offset, size, digest = None, 0, hashlib.sha256(b"").hexdigest()
        else:
            offset, raw = vec
            size, digest = len(raw), hashlib.sha256(raw).hexdigest()
        buffers.append({"index": i, "data_offset": offset, "size": size,
                        "sha256": digest, "name": fb.string(t, 1)})

    subgraphs = []
    op_sequence = []
    tensor_types = []
    for sg_i, sg in enumerate(fb.vector_tables(root, 2)):
        tensors = []
        for ti, t in enumerate(fb.vector_tables(sg, 0)):
            q_field = fb.field(t, 4)
            q = fb.target(q_field, "quantization") if q_field is not None else None
            quant = None
            if q is not None:
                scales = fb.vector_scalars(q, 2, "f32")
                zeros = fb.vector_scalars(q, 3, "i64")
                quant = {"scale": scales, "zero_point": zeros,
                         "quantized_dimension": fb.scalar(q, 6, "i32", 0),
                         "min": fb.vector_scalars(q, 0, "f32"),
                         "max": fb.vector_scalars(q, 1, "f32")}
            shape = fb.vector_scalars(t, 0, "i32")
            typ = fb.scalar(t, 1, "u8", 0)
            item = {"index": ti, "name": fb.string(t, 3, ""), "shape": shape,
                    "type": TYPES.get(typ, f"TYPE_{typ}"), "type_code": typ,
                    "buffer": fb.scalar(t, 2, "u32", 0), "quantization": quant}
            tensors.append(item)
            tensor_types.append(item)
        sequence = []
        for oi, op in enumerate(fb.vector_tables(sg, 3)):
            opcode_index = fb.scalar(op, 0, "u32", 0)
            code = ops[opcode_index] if opcode_index < len(ops) else None
            operator = code["builtin"] if code else f"OPCODE_{opcode_index}"
            options_type, options = decode_options(fb, op, operator, tensors, buffers)
            item = {"index": oi, "opcode_index": opcode_index,
                    "operator": operator,
                    "builtin_code": code["builtin_code"] if code else None,
                    "builtin_options_type": options_type,
                    "builtin_options": options,
                    "inputs": fb.vector_scalars(op, 1, "i32"),
                    "outputs": fb.vector_scalars(op, 2, "i32")}
            sequence.append(item)
            op_sequence.append({"subgraph": sg_i, **item})
        subgraphs.append({"index": sg_i, "name": fb.string(sg, 4, ""),
                          "inputs": fb.vector_scalars(sg, 1, "i32"),
                          "outputs": fb.vector_scalars(sg, 2, "i32"),
                          "tensors": tensors, "operators": sequence})

    return {"format": "TFLite FlatBuffer", "identifier": "TFL3",
            "source": source_name, "model_version": version,
            "operator_codes": ops, "operator_sequence": op_sequence,
            "subgraphs": subgraphs, "buffers": buffers,
            "memory_summary": memory_summary(subgraphs, buffers),
            "model": {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}}


def extract_cc(path: Path) -> bytes:
    text = path.read_text(encoding="utf-8")
    begin = text.find("pretrainedResnet_quant_tflite[]")
    if begin < 0:
        raise ParseError("embedded model declaration not found")
    open_brace = text.find("{", begin)
    close = text.find("};", open_brace)
    if open_brace < 0 or close < 0:
        raise ParseError("embedded model initializer not found")
    values = re.findall(r"0x([0-9a-fA-F]+)", text[open_brace:close])
    raw = bytes(int(v, 16) for v in values)
    declared = re.search(r"pretrainedResnet_quant_tflite_len\s*=\s*(\d+)", text)
    if declared and int(declared.group(1)) != len(raw):
        raise ParseError(f"embedded length says {declared.group(1)}, parsed {len(raw)}")
    return raw


def manifest_self_checks(manifest: dict) -> None:
    """Regression checks for the exact pretrainedResnet quantized topology."""
    if len(manifest["operator_sequence"]) != 16:
        raise AssertionError("expected 16 operators")
    expected = ["CONV_2D", "CONV_2D", "CONV_2D", "ADD", "CONV_2D",
                "CONV_2D", "CONV_2D", "ADD", "CONV_2D", "CONV_2D",
                "CONV_2D", "ADD", "AVERAGE_POOL_2D", "RESHAPE",
                "FULLY_CONNECTED", "SOFTMAX"]
    actual = [item["operator"] for item in manifest["operator_sequence"]]
    if actual != expected:
        raise AssertionError(f"unexpected operator sequence: {actual}")
    if manifest["operator_codes"][6]["builtin_code"] != 114 or manifest["operator_codes"][6]["builtin"] != "QUANTIZE":
        raise AssertionError("builtin operator enum 114 must be QUANTIZE")
    sg = manifest["subgraphs"][0]
    if sg["inputs"] != [0] or sg["outputs"] != [37]:
        raise AssertionError("unexpected subgraph IO")
    tensors = {t["index"]: t for t in sg["tensors"]}
    if tensors[0]["shape"] != [1, 32, 32, 3] or tensors[37]["shape"] != [1, 10]:
        raise AssertionError("unexpected input/output shape")
    if tensors[0]["quantization"]["scale"] != [1.0] or tensors[0]["quantization"]["zero_point"] != [-128]:
        raise AssertionError("unexpected input quantization")
    if tensors[37]["quantization"]["scale"] != [0.00390625] or tensors[37]["quantization"]["zero_point"] != [-128]:
        raise AssertionError("unexpected output quantization")
    for index, shape in {22: [1, 32, 32, 16], 26: [1, 16, 16, 32],
                         30: [1, 8, 8, 64], 34: [1, 1, 1, 64]}.items():
        if tensors[index]["shape"] != shape:
            raise AssertionError(f"unexpected topology shape for tensor {index}")
    reshape = manifest["operator_sequence"][13]["builtin_options"]
    if reshape.get("new_shape") != [-1, 64]:
        raise AssertionError("reshape constant was not decoded")


def self_checks(model: bytes) -> None:
    # These checks deliberately exercise the validation path, not just happy
    # path parsing.  A future loosening of bounds checks therefore fails fast.
    for bad in (b"bad", b"\x08\x00\x00\x00NOPE" + b"\x00" * 8):
        try:
            FB(bad).root()
        except ParseError:
            pass
        else:
            raise AssertionError("invalid TFL3 self-check unexpectedly succeeded")
    corrupt = bytearray(model)
    corrupt[0:4] = struct.pack("<I", len(model) + 100)
    try:
        FB(bytes(corrupt)).root()
    except ParseError:
        pass
    else:
        raise AssertionError("malformed root offset self-check unexpectedly succeeded")
    manifest_self_checks(parse_model(model, "self-check"))


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parents[1]
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", type=Path,
                    default=here / "third_party/mlcommons_tiny/benchmark/training/image_classification/trained_models/pretrainedResnet_quant.tflite")
    ap.add_argument("--embedded", type=Path,
                    default=here / "third_party/mlcommons_tiny/benchmark/reference_submissions/image_classification/ic/ic_model_quant_data.cc")
    ap.add_argument("--output", type=Path, default=here / "model/model_manifest.json")
    args = ap.parse_args(argv)
    model = args.model.read_bytes()
    self_checks(model)
    embedded = extract_cc(args.embedded)
    if model != embedded:
        raise SystemExit("model bytes do not exactly match embedded ic_model_quant_data.cc")
    result = parse_model(model, str(args.model))
    manifest_self_checks(result)
    result["embedded_match"] = {"path": str(args.embedded), "size": len(embedded),
                                 "sha256": hashlib.sha256(embedded).hexdigest(), "exact": True}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    print(f"wrote {args.output} ({len(model)} bytes, sha256={result['model']['sha256']})")
    print(f"operators={len(result['operator_sequence'])} tensors={len(result['subgraphs'][0]['tensors']) if result['subgraphs'] else 0} buffers={len(result['buffers'])}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ParseError, AssertionError) as exc:
        print(f"tflite_introspect: ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
