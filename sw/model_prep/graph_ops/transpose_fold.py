"""Fold a Transpose feeding a fully-connected layer (Gemm/MatMul) into the FC weight.

Issue #14 (equivalence-preserving graph surgery). tf2onnx-exported Keras models often keep the TF
NHWC element order alive across the final Dense by inserting an ``NCHW->NHWC`` Transpose before the
flatten+MatMul. That standalone Transpose (perm like ``[0, 2, 3, 1]``) is an op CoreDLA would rather
not carry. Because the FC weight is a constant, the permutation can be *absorbed into the weight's
contracting axis*: reorder the weight rows the same way the Transpose reorders the flattened
activation, drop the Transpose, and the FC output is bit-for-bit unchanged (pure index shuffle of a
constant -- MLPerf Tiny Closed-Division legal).

Two patterns are handled:

* **activation-side flatten transpose** -- ``Transpose -> Reshape(flatten) -> Gemm/MatMul``. The
  Reshape is rewired onto the Transpose's input and the weight rows are permuted to match the new
  flatten order. This is the "NCHW->NHWC flatten before a Dense" case.
* **weight-side transpose** -- the FC weight input is itself ``Transpose(initializer)``. The
  transpose is applied to the constant at build time and the node removed.
"""

from __future__ import annotations

import numpy as np
import onnx
from onnx import numpy_helper, shape_inference


def _shapes(model: onnx.ModelProto) -> dict[str, list]:
    inferred = shape_inference.infer_shapes(model)
    out: dict[str, list] = {}
    for vi in list(inferred.graph.value_info) + list(inferred.graph.input) + list(
        inferred.graph.output
    ):
        dims = []
        for d in vi.type.tensor_type.shape.dim:
            dims.append(d.dim_value if d.HasField("dim_value") else d.dim_param)
        out[vi.name] = dims
    return out


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


def flatten_row_permutation(pre_shape: list[int], perm: list[int]) -> np.ndarray:
    """Row permutation that rewrites a weight from *transposed*-flatten order to *pre*-flatten order.

    ``pre_shape`` is the non-batch shape of the tensor *before* the Transpose (e.g. ``[C, H, W]``);
    ``perm`` is the non-batch permutation the Transpose applies (0-indexed over those axes). The
    original op flattens the transposed tensor (shape ``pre_shape[perm]``) row-major; after we drop
    the Transpose the flatten runs row-major over ``pre_shape``. Returns ``old_rows`` such that
    ``W_new = W_old[old_rows]`` gives an FC that consumes the un-transposed flatten yet produces the
    identical result.
    """
    pre_shape = [int(x) for x in pre_shape]
    perm = [int(p) for p in perm]
    s_t = [pre_shape[p] for p in perm]  # shape after the transpose
    # idx[:, j] = multi-index (over pre_shape, row-major) of new-flatten position j
    idx = np.indices(pre_shape).reshape(len(pre_shape), -1)
    # the same element sits at multi-index idx[perm] in the transposed tensor
    old_rows = np.ravel_multi_index(idx[perm], s_t)
    return old_rows


def _producer_map(model):
    return {o: n for n in model.graph.node for o in n.output}


def _init_map(model):
    return {i.name: i for i in model.graph.initializer}


def _weight_axis_length(fc: onnx.NodeProto, w_arr: np.ndarray) -> tuple[int, int]:
    """Return (contracting_axis, K) for the FC's constant weight."""
    if fc.op_type == "MatMul":
        return 0, w_arr.shape[0]
    # Gemm: Y = A(^T) * B(^T); weight B is input[1]. contracting axis is 0 unless transB.
    transb = _attr_int(fc, "transB", 0)
    axis = 1 if transb else 0
    return axis, w_arr.shape[axis]


def fold_transposes(
    model: onnx.ModelProto, *, inplace: bool = False
) -> tuple[onnx.ModelProto, list[dict]]:
    """Fold every foldable Transpose->FC pattern into the FC weight.

    Returns ``(new_model, changes)``. The input ``model`` is untouched unless ``inplace=True``.
    """
    if not inplace:
        model = _clone(model)
    changes: list[dict] = []
    changed = True
    while changed:
        changed = False
        shapes = _shapes(model)
        producers = _producer_map(model)
        inits = _init_map(model)
        for fc in list(model.graph.node):
            if fc.op_type not in ("MatMul", "Gemm"):
                continue
            # locate constant weight input and the activation input
            weight_in = next((i for i in fc.input if i in inits), None)
            if weight_in is None:
                # weight might be Transpose(const) -- pattern 2
                if _try_fold_weight_transpose(model, fc, producers, inits, changes):
                    changed = True
                    break
                continue
            act_in = next((i for i in fc.input if i != weight_in), None)
            if act_in is None:
                continue
            reshape = producers.get(act_in)
            if reshape is None or reshape.op_type not in ("Reshape", "Flatten"):
                continue
            transpose = producers.get(reshape.input[0])
            if transpose is None or transpose.op_type != "Transpose":
                continue
            if _fold_activation_transpose(
                model, fc, weight_in, reshape, transpose, shapes, inits, changes
            ):
                changed = True
                break
    return model, changes


def _fold_activation_transpose(model, fc, weight_in, reshape, transpose, shapes, inits, changes):
    perm = _attr_ints(transpose, "perm")
    x_name = transpose.input[0]
    x_shape = shapes.get(x_name)
    if perm is None or x_shape is None:
        return False
    if perm[0] != 0 or any(not isinstance(d, int) for d in x_shape[1:]):
        return False  # only batch-preserving perms over concrete non-batch dims
    pre_shape = x_shape[1:]
    nb_perm = [p - 1 for p in perm[1:]]
    k = int(np.prod(pre_shape))

    w_arr = numpy_helper.to_array(inits[weight_in])
    axis, wk = _weight_axis_length(fc, w_arr)
    if wk != k:
        return False  # weight contracting length must equal the flattened window
    old_rows = flatten_row_permutation(pre_shape, nb_perm)
    w_new = np.take(w_arr, old_rows, axis=axis)

    # replace weight initializer in place
    new_init = numpy_helper.from_array(np.ascontiguousarray(w_new), name=weight_in)
    _replace_initializer(model, weight_in, new_init)
    # rewire reshape onto the pre-transpose tensor and drop the transpose
    for i, name in enumerate(reshape.input):
        if name == transpose.output[0]:
            reshape.input[i] = x_name
    _remove_node_if_unused(model, transpose)
    del model.graph.value_info[:]
    changes.append(
        {
            "pattern": "activation_flatten_transpose",
            "fc": fc.name,
            "fc_op": fc.op_type,
            "transpose": transpose.name,
            "perm": perm,
            "weight": weight_in,
            "contracting_axis": axis,
            "K": k,
        }
    )
    return True


def _try_fold_weight_transpose(model, fc, producers, inits, changes) -> bool:
    for idx, name in enumerate(fc.input):
        prod = producers.get(name)
        if prod is None or prod.op_type != "Transpose":
            continue
        src = prod.input[0]
        if src not in inits:
            continue
        perm = _attr_ints(prod, "perm")
        arr = numpy_helper.to_array(inits[src])
        if perm is None:
            perm = list(range(arr.ndim))[::-1]
        new_arr = np.ascontiguousarray(np.transpose(arr, perm))
        new_name = f"{src}_T"
        model.graph.initializer.append(numpy_helper.from_array(new_arr, name=new_name))
        fc.input[idx] = new_name
        _remove_node_if_unused(model, prod)
        _prune_unused_initializer(model, src)
        del model.graph.value_info[:]
        changes.append(
            {
                "pattern": "weight_transpose",
                "fc": fc.name,
                "fc_op": fc.op_type,
                "transpose": prod.name,
                "perm": perm,
                "weight": src,
            }
        )
        return True
    return False


def _replace_initializer(model, name, new_init):
    for i, init in enumerate(model.graph.initializer):
        if init.name == name:
            del model.graph.initializer[i]
            model.graph.initializer.insert(i, new_init)
            return
    model.graph.initializer.append(new_init)


def _remove_node_if_unused(model, node):
    consumers = [
        n for n in model.graph.node if n is not node and any(i in node.output for i in n.input)
    ]
    graph_out = {o.name for o in model.graph.output}
    if consumers or (graph_out & set(node.output)):
        return  # still needed elsewhere
    for i, n in enumerate(model.graph.node):
        if n is node:
            del model.graph.node[i]
            return


def _prune_unused_initializer(model, name):
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
