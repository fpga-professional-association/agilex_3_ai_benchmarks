"""Rewrite depthwise (grouped) Conv nodes into a groups=1 dense Conv.

Part of the DDR-free ds-cnn track (``docs/onboard_benchmark_plan.md`` Track B2 / §0). CoreDLA's
DDR-free flow rejects any op that needs "slicing" -- and a grouped ``Conv`` (``group > 1``, the ONNX
encoding of a depthwise/grouped convolution; OpenVINO's importer lowers it to ``GroupConvolution``)
is lowered by the compiler into per-group split + concat, which DDR-free does not support (whole
activation tensor must live on-chip, no slicing). ds-cnn-kws has four true depthwise convs
(``[C,1,kh,kw]``, ``group == C``) plus, after ``pool_decompose``'s ``"conv"`` strategy replaces its
oversized 25x5 ``AveragePool`` with an exact-mean depthwise conv, a fifth structurally-identical
grouped conv -- this module treats any ``group > 1`` Conv uniformly, regardless of origin.

**The rewrite:** a grouped Conv computes, per group ``g``, ``out[:, g] = conv(in[:, g], w[g])`` where
``w`` has shape ``[Cout, Cin/group, kh, kw]``. Zero-pad ``w`` into a dense ``[Cout, Cin, kh, kw]``
tensor whose only nonzero content is the per-group block placed at its own input-channel slice (a
block-diagonal weight, degenerating to a true per-channel diagonal for the fully-depthwise case
``group == Cin == Cout``) and drop the ``group`` attribute (implicit default 1). Every output channel
then only "sees" (multiplies by zero elsewhere) its own group's input channels, so the dense conv's
mathematics is **identical** to the grouped conv's: a pure zero-padding of the weight, no numeric
change to any real filter tap. For the fully-depthwise case (``cin_per_group == 1``, ds-cnn's four
layers) each output channel has exactly one nonzero term, so there is nothing to sum and the result
is bit-exact; for a general grouped conv (``cin_per_group > 1``) the dense conv's underlying kernel
sums over more (all-zero) terms in a different order than the grouped kernel's, so float
non-associativity can leave ~1e-7-level noise -- immaterial, and still exactly reproducible in
fixed-point (a per-tap multiply by an exact 0 stays exactly 0 after quantization, no accumulation-
order dependence once every operand is an integer). Cost: the dense weight is ``group``x larger
(mostly zeros) and compute grows accordingly -- the whole point of doing this is to trade that
(cheap for a small ``group``/small kernel, potentially large for a wide grouped conv) against the
concat blocker.

Requires the grouped Conv's weight to be a constant initializer (true of every depthwise conv found
in ds-cnn-kws's exported ONNX, both the four original ones and the pool-decompose-produced one).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import onnx
from onnx import numpy_helper


@dataclass
class GroupedConvInfo:
    """A ``Conv`` node with ``group > 1``."""

    node: onnx.NodeProto
    index: int  # position in graph.node
    group: int


def _attr_int(node: onnx.NodeProto, name: str, default: int) -> int:
    for a in node.attribute:
        if a.name == name:
            return int(a.i)
    return default


def _set_or_remove_group_attr(node: onnx.NodeProto) -> None:
    for i, a in enumerate(node.attribute):
        if a.name == "group":
            del node.attribute[i]
            return


def find_grouped_convs(model: onnx.ModelProto) -> list[GroupedConvInfo]:
    """Return :class:`GroupedConvInfo` for every ``Conv`` node with ``group > 1``."""
    found = []
    for i, node in enumerate(model.graph.node):
        if node.op_type != "Conv":
            continue
        group = _attr_int(node, "group", 1)
        if group > 1:
            found.append(GroupedConvInfo(node, i, group))
    return found


def dense_weight_from_grouped(w: np.ndarray, group: int) -> np.ndarray:
    """Zero-pad a grouped Conv weight ``[Cout, Cin/group, kh, kw]`` into a dense, block-diagonal
    ``[Cout, Cin, kh, kw]`` weight for the equivalent ``group=1`` Conv. Bit-exact: every nonzero
    entry is copied verbatim from ``w``; everything else is an exact ``0``.
    """
    if w.ndim != 4:
        raise ValueError(f"expected a 4-D Conv weight [Cout, Cin/group, kh, kw], got shape {w.shape}")
    cout, cin_per_group, kh, kw = (int(d) for d in w.shape)
    if cout % group != 0:
        raise ValueError(f"Cout={cout} not divisible by group={group}")
    cout_per_group = cout // group
    cin = cin_per_group * group
    dense = np.zeros((cout, cin, kh, kw), dtype=w.dtype)
    for g in range(group):
        out_lo, out_hi = g * cout_per_group, (g + 1) * cout_per_group
        in_lo, in_hi = g * cin_per_group, (g + 1) * cin_per_group
        dense[out_lo:out_hi, in_lo:in_hi, :, :] = w[out_lo:out_hi, :, :, :]
    return dense


def rewrite_grouped_convs_to_dense(
    model: onnx.ModelProto, *, inplace: bool = False
) -> tuple[onnx.ModelProto, list[dict]]:
    """Replace every ``group > 1`` ``Conv`` in ``model`` with an equivalent ``group=1`` dense Conv.

    Returns ``(new_model, changes)`` where each change dict records the rewritten node, its original
    group/weight shape, and the resulting dense weight shape (for reporting the weight-size cost).
    The input ``model`` is left untouched unless ``inplace=True``. Raises ``ValueError`` if a grouped
    Conv's weight is not a constant initializer (nothing to dense-ify).
    """
    if not inplace:
        model = _clone(model)
    changes: list[dict] = []
    for info in find_grouped_convs(model):
        node = model.graph.node[info.index]
        w_name = node.input[1]
        inits = {i.name: i for i in model.graph.initializer}
        if w_name not in inits:
            raise ValueError(
                f"grouped Conv {node.name!r} (group={info.group}) weight {w_name!r} is not a "
                f"constant initializer -- cannot dense-ify without a fold-able weight"
            )
        w = numpy_helper.to_array(inits[w_name])
        dense = dense_weight_from_grouped(w, info.group)
        new_name = f"{w_name}__dense"
        new_init = numpy_helper.from_array(np.ascontiguousarray(dense), name=new_name)
        model.graph.initializer.append(new_init)
        node.input[1] = new_name
        _set_or_remove_group_attr(node)
        _prune_unused_initializer(model, w_name)
        changes.append(
            {
                "node": node.name,
                "group": info.group,
                "weight": w_name,
                "grouped_shape": list(w.shape),
                "dense_weight": new_name,
                "dense_shape": list(dense.shape),
                "growth_x": dense.size / w.size,
            }
        )
    del model.graph.value_info[:]
    return model, changes


def _prune_unused_initializer(model: onnx.ModelProto, name: str) -> None:
    referenced = any(name == i for n in model.graph.node for i in n.input)
    if referenced:
        return
    for i, init in enumerate(model.graph.initializer):
        if init.name == name:
            del model.graph.initializer[i]
            return


def _clone(model: onnx.ModelProto) -> onnx.ModelProto:
    m = onnx.ModelProto()
    m.CopyFrom(model)
    return m
