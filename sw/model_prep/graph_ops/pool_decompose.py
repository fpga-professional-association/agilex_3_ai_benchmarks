"""Decompose oversized Average/GlobalAveragePool nodes into CoreDLA-acceptable subgraphs.

Issue #14 (equivalence-preserving graph surgery). CoreDLA's ``pool`` primitive on the
``AGX3_Performance.arch`` caps windows at 3x3 and strides at 4x4 (see the ``pool { ... }`` block:
``max_window_height/width : 3``, ``max_stride_vertical/horizontal : 4``). Models such as
ds-cnn-kws (AveragePool 25x5) and resnet8-cifar10 (AveragePool 8x8) exceed that and are rejected.

This module replaces such a pool with a subgraph that computes the **exact same** floating-point
average (MLPerf Tiny Closed-Division legal: the mathematics is unchanged), using one of three
strategies so the per-model agent can pick whichever the compiler actually accepts:

* ``"conv"``   -- a single depthwise Conv whose every kernel weight is ``1/(kh*kw)`` (a true mean).
                  Conv kernels are bounded by ``filter_size_{width,height}_max : 28`` in the arch,
                  not by the pool ceiling, so a 25x5 mean fits.
* ``"cascade"``-- a cascade of small AveragePool ops, each with window<=3 / stride<=4, whose
                  composition equals the original mean. The window is factored into <=3 factors;
                  any non-factorable residual (e.g. the prime 5 in ds-cnn's 25x5) is finished with a
                  small depthwise Conv. When a window is fully factorable (e.g. 8 = 2*2*2 in
                  resnet8) the result is pure pooling.
* ``"reduce_mean"`` -- a single ReduceMean over the pooled spatial axes (exact; offered as an
                  alternative for compilers that prefer a reduction op).

Averaging in equal, non-overlapping groups is associative, so "mean of means over equal groups"
equals the overall mean; the only deviation from the original op is floating-point summation order,
which stays within ~1e-6 for these sizes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper, shape_inference

MAX_WINDOW = 3
MAX_STRIDE = 4

STRATEGIES = ("conv", "cascade", "reduce_mean")


@dataclass
class PoolInfo:
    """Geometry of one pooling node, resolved against inferred shapes."""

    node: onnx.NodeProto
    index: int  # position in graph.node
    channels: int
    kh: int
    kw: int
    sh: int
    sw: int
    is_global: bool

    @property
    def oversized(self) -> bool:
        return (
            self.kh > MAX_WINDOW
            or self.kw > MAX_WINDOW
            or self.sh > MAX_STRIDE
            or self.sw > MAX_STRIDE
        )


def _attr_ints(node: onnx.NodeProto, name: str):
    for a in node.attribute:
        if a.name == name:
            return list(a.ints)
    return None


def _attr_int(node: onnx.NodeProto, name: str, default: int) -> int:
    for a in node.attribute:
        if a.name == name:
            return int(a.i)
    return default


def _shapes(model: onnx.ModelProto) -> dict[str, list[int | str]]:
    inferred = shape_inference.infer_shapes(model)
    out: dict[str, list[int | str]] = {}
    for vi in list(inferred.graph.value_info) + list(inferred.graph.input) + list(
        inferred.graph.output
    ):
        dims = []
        for d in vi.type.tensor_type.shape.dim:
            dims.append(d.dim_value if d.HasField("dim_value") else d.dim_param)
        out[vi.name] = dims
    return out


def _pool_info(node: onnx.NodeProto, index: int, shapes: dict) -> PoolInfo:
    in_shape = shapes.get(node.input[0])
    if in_shape is None or len(in_shape) != 4:
        raise ValueError(
            f"pool node {node.name!r}: input {node.input[0]!r} shape unknown/not 4-D "
            f"(got {in_shape}); cannot decompose without a resolved N,C,H,W shape"
        )
    _, c, h, w = in_shape
    for label, val in (("channels", c), ("H", h), ("W", w)):
        if not isinstance(val, int):
            raise ValueError(
                f"pool node {node.name!r}: {label} is symbolic ({val!r}); decomposition needs a "
                f"concrete channel/spatial size"
            )
    if node.op_type == "GlobalAveragePool":
        kh, kw, sh, sw, is_global = h, w, h, w, True
    else:
        pads = _attr_ints(node, "pads") or [0, 0, 0, 0]
        if any(p != 0 for p in pads):
            raise ValueError(
                f"pool node {node.name!r}: non-zero pads {pads} not supported (average semantics "
                f"with count_include_pad differ from a constant conv kernel)"
            )
        if _attr_int(node, "count_include_pad", 0) not in (0, 1):
            raise ValueError(f"pool node {node.name!r}: unexpected count_include_pad")
        ks = _attr_ints(node, "kernel_shape")
        if ks is None:
            raise ValueError(f"AveragePool {node.name!r} has no kernel_shape")
        kh, kw = ks
        strides = _attr_ints(node, "strides") or [1, 1]
        sh, sw = strides
        is_global = False
    return PoolInfo(node, index, c, kh, kw, sh, sw, is_global)


def find_oversized_pools(model: onnx.ModelProto) -> list[PoolInfo]:
    """Return :class:`PoolInfo` for every AveragePool/GlobalAveragePool that exceeds the arch ceiling."""
    shapes = _shapes(model)
    found = []
    for i, node in enumerate(model.graph.node):
        if node.op_type in ("AveragePool", "GlobalAveragePool"):
            info = _pool_info(node, i, shapes)
            if info.oversized:
                found.append(info)
    return found


def factorize_into(n: int, max_factor: int = MAX_WINDOW) -> tuple[list[int], int]:
    """Greedily factor ``n`` into factors each ``<= max_factor``.

    Returns ``(factors, residual)`` with ``prod(factors) * residual == n`` and ``residual`` holding
    the part that has no ``<= max_factor`` divisor (1 when ``n`` is fully factorable). Factors are
    returned largest-first. Example: ``factorize_into(8) -> ([2, 2, 2], 1)``;
    ``factorize_into(25) -> ([], 25)``; ``factorize_into(12) -> ([3, 2, 2], 1)``.
    """
    if n < 1:
        raise ValueError(f"factorize_into: n must be >= 1, got {n}")
    factors: list[int] = []
    r = n
    f = max_factor
    while f >= 2:
        while r % f == 0:
            factors.append(f)
            r //= f
        f -= 1
    factors.sort(reverse=True)
    return factors, r


def _dtype(model: onnx.ModelProto) -> int:
    for vi in model.graph.input:
        t = vi.type.tensor_type.elem_type
        if t in (TensorProto.FLOAT, TensorProto.FLOAT16, TensorProto.DOUBLE):
            return t
    return TensorProto.FLOAT


def _np_dtype(elem: int):
    return {TensorProto.FLOAT: np.float32, TensorProto.FLOAT16: np.float16, TensorProto.DOUBLE: np.float64}[
        elem
    ]


def _depthwise_conv_nodes(info: PoolInfo, x_name: str, out_name: str, elem: int, tag: str):
    """A single depthwise Conv (group=C) with constant 1/(kh*kw) weights = an exact mean."""
    kh, kw, c = info.kh, info.kw, info.channels
    w = np.full((c, 1, kh, kw), 1.0 / (kh * kw), dtype=_np_dtype(elem))
    w_name = f"{tag}_w"
    init = numpy_helper.from_array(w, name=w_name)
    node = helper.make_node(
        "Conv",
        inputs=[x_name, w_name],
        outputs=[out_name],
        name=f"{tag}_conv",
        kernel_shape=[kh, kw],
        strides=[info.sh, info.sw],
        pads=[0, 0, 0, 0],
        group=c,
    )
    return [node], [init]


def _cascade_nodes(info: PoolInfo, x_name: str, out_name: str, elem: int, tag: str):
    """A cascade of <=3 window AvgPools (+ a residual depthwise Conv for non-factorable sizes)."""
    if (info.sh, info.sw) != (info.kh, info.kw):
        raise ValueError(
            f"pool {info.node.name!r}: cascade strategy requires stride==kernel (non-overlapping "
            f"tiling); got kernel {info.kh}x{info.kw} stride {info.sh}x{info.sw}. Use strategy='conv'."
        )
    hf, hr = factorize_into(info.kh)
    wf, wr = factorize_into(info.kw)
    nodes: list[onnx.NodeProto] = []
    inits: list[onnx.TensorProto] = []
    cur = x_name
    n_stages = max(len(hf), len(wf))
    stage_shape_h, stage_shape_w = info.kh, info.kw
    for s in range(n_stages):
        khi = hf[s] if s < len(hf) else 1
        kwi = wf[s] if s < len(wf) else 1
        stage_shape_h //= khi
        stage_shape_w //= kwi
        last = s == n_stages - 1 and hr == 1 and wr == 1
        nxt = out_name if last else f"{tag}_pool{s}"
        nodes.append(
            helper.make_node(
                "AveragePool",
                inputs=[cur],
                outputs=[nxt],
                name=f"{tag}_pool{s}",
                kernel_shape=[khi, kwi],
                strides=[khi, kwi],
            )
        )
        cur = nxt
    if hr > 1 or wr > 1:
        # Finish the non-factorable residual (e.g. 5) with an exact depthwise-conv mean.
        residual = PoolInfo(info.node, info.index, info.channels, hr, wr, hr, wr, info.is_global)
        cnodes, cinits = _depthwise_conv_nodes(residual, cur, out_name, elem, f"{tag}_res")
        nodes += cnodes
        inits += cinits
    elif n_stages == 0:
        raise ValueError(
            f"pool {info.node.name!r}: window {info.kh}x{info.kw} has no <=3 factor and no residual; "
            f"unreachable"
        )
    return nodes, inits


def _reduce_mean_nodes(info: PoolInfo, x_name: str, out_name: str, elem: int, tag: str):
    """A single ReduceMean over the spatial axes (keepdims) -- exact for a full-window pool."""
    if not (info.is_global or (info.kh, info.kw) == (info.kh, info.kw)):  # always true; kept explicit
        pass
    node = helper.make_node(
        "ReduceMean",
        inputs=[x_name],
        outputs=[out_name],
        name=f"{tag}_reducemean",
        axes=[2, 3],
        keepdims=1,
    )
    return [node], []


_BUILDERS = {
    "conv": _depthwise_conv_nodes,
    "cascade": _cascade_nodes,
    "reduce_mean": _reduce_mean_nodes,
}


def decompose_pools(
    model: onnx.ModelProto,
    strategy: str = "auto",
    *,
    inplace: bool = False,
) -> tuple[onnx.ModelProto, list[dict]]:
    """Replace every oversized pool in ``model`` with an equivalent subgraph.

    ``strategy`` is one of ``"conv"``, ``"cascade"``, ``"reduce_mean"`` or ``"auto"``. ``"auto"``
    picks ``"cascade"`` when the window is fully factorable into <=3 steps (pure pooling, the most
    CoreDLA-native result) and ``"conv"`` otherwise. Returns ``(new_model, changes)`` where each
    change dict records the replaced node and the ops it became. The original ``model`` is left
    untouched unless ``inplace=True``.
    """
    if strategy not in STRATEGIES and strategy != "auto":
        raise ValueError(f"unknown strategy {strategy!r}; choose from {STRATEGIES} or 'auto'")
    if not inplace:
        model = _clone(model)
    elem = _dtype(model)
    changes: list[dict] = []
    # Recompute geometry each pass because indices shift as we splice.
    while True:
        pools = find_oversized_pools(model)
        if not pools:
            break
        info = pools[0]
        node = model.graph.node[info.index]
        x_name = node.input[0]
        out_name = node.output[0]
        tag = _sanitize(node.name or node.output[0] or f"pool{info.index}")

        chosen = strategy
        if strategy == "auto":
            hf, hr = factorize_into(info.kh)
            wf, wr = factorize_into(info.kw)
            non_overlapping = (info.sh, info.sw) == (info.kh, info.kw)
            chosen = "cascade" if (hr == 1 and wr == 1 and non_overlapping) else "conv"

        new_nodes, new_inits = _BUILDERS[chosen](info, x_name, out_name, elem, tag)
        del model.graph.node[info.index]
        for offset, nn in enumerate(new_nodes):
            model.graph.node.insert(info.index + offset, nn)
        model.graph.initializer.extend(new_inits)
        changes.append(
            {
                "op": node.op_type,
                "node": node.name,
                "window": [info.kh, info.kw],
                "stride": [info.sh, info.sw],
                "channels": info.channels,
                "strategy": chosen,
                "replaced_with": [n.op_type for n in new_nodes],
            }
        )
    # value_info can be stale after splicing; drop it so downstream re-infers cleanly.
    del model.graph.value_info[:]
    return model, changes


def _sanitize(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name)[:60] or "pool"


def _clone(model: onnx.ModelProto) -> onnx.ModelProto:
    m = onnx.ModelProto()
    m.CopyFrom(model)
    return m
