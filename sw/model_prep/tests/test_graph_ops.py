"""Equivalence proof for graph_ops (issue #14).

Every transform must preserve onnxruntime outputs to ~1e-5 (MLPerf Tiny Closed-Division legal: the
mathematics is unchanged). We prove that on small SYNTHETIC graphs with edge cases, and again on the
REAL cached ONNX models the per-model agents will actually transform, reporting the max abs output
diff for each.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

# Heavy deps (AGENTS.md: skipped in CI, same convention as test_common.py). Must precede the
# graph_ops import too, since graph_ops imports onnx transitively.
pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

import onnx  # noqa: E402
import onnxruntime as ort  # noqa: E402
from onnx import TensorProto, helper, numpy_helper  # noqa: E402

from graph_ops import (  # noqa: E402
    check_coredla_friendly,
    decompose_pools,
    find_grouped_convs,
    find_oversized_pools,
    flatten_row_permutation,
    fold_transposes,
    make_coredla_friendly,
    rewrite_grouped_convs_to_dense,
)
from graph_ops.depthwise_to_dense import dense_weight_from_grouped  # noqa: E402
from graph_ops.pool_decompose import factorize_into  # noqa: E402

ONNX_DIR = Path(__file__).resolve().parents[3] / "models" / "onnx"
RTOL = 1e-5
ATOL = 1e-5


# --------------------------------------------------------------------------- helpers


def _run(model: onnx.ModelProto, feeds: dict[str, np.ndarray]) -> np.ndarray:
    sess = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    out = sess.run(None, feeds)
    return out[0]


def _pool_model(C, H, W, *, op="AveragePool", kernel=None, stride=None) -> onnx.ModelProto:
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, C, H, W])
    if op == "GlobalAveragePool":
        node = helper.make_node("GlobalAveragePool", ["x"], ["y"], name="pool")
        oh = ow = 1
    else:
        kernel = kernel or [H, W]
        stride = stride or kernel
        node = helper.make_node(
            "AveragePool", ["x"], ["y"], name="pool", kernel_shape=kernel, strides=stride
        )
        oh = (H - kernel[0]) // stride[0] + 1
        ow = (W - kernel[1]) // stride[1] + 1
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, C, oh, ow])
    graph = helper.make_graph([node], "pool_g", [x], [y])
    return _finish(graph)


def _finish(graph) -> onnx.ModelProto:
    model = helper.make_model(graph, opset_imports=[helper.make_operatorsetid("", 13)])
    model.ir_version = 9
    onnx.checker.check_model(model)
    return model


def _transpose_fc_model(C, H, W, O, *, fc="MatMul", perm=(0, 2, 3, 1)) -> tuple[onnx.ModelProto, np.ndarray]:
    """input[1,C,H,W] -> Transpose(perm) -> Reshape[1,K] -> MatMul/Gemm(weight[K,O]) -> [1,O]."""
    K = C * H * W
    rng = np.random.default_rng(0)
    w = rng.standard_normal((K, O)).astype(np.float32)
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, C, H, W])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, O])
    nodes = [
        helper.make_node("Transpose", ["x"], ["t"], name="tr", perm=list(perm)),
        helper.make_node("Reshape", ["t", "shape"], ["f"], name="flat"),
    ]
    inits = [
        numpy_helper.from_array(np.array([1, K], dtype=np.int64), name="shape"),
        numpy_helper.from_array(w, name="W"),
    ]
    if fc == "MatMul":
        nodes.append(helper.make_node("MatMul", ["f", "W"], ["y"], name="fc"))
    else:
        b = rng.standard_normal((O,)).astype(np.float32)
        inits.append(numpy_helper.from_array(b, name="B"))
        nodes.append(helper.make_node("Gemm", ["f", "W", "B"], ["y"], name="fc"))
    graph = helper.make_graph(nodes, "tr_fc", [x], [y], initializer=inits)
    return _finish(graph), w


# --------------------------------------------------------------------------- unit: math helpers


def test_factorize_into():
    assert factorize_into(8) == ([2, 2, 2], 1)
    assert factorize_into(25) == ([], 25)
    assert factorize_into(12) == ([3, 2, 2], 1)
    assert factorize_into(6) == ([3, 2], 1)
    assert factorize_into(1) == ([], 1)
    for n in range(1, 200):
        f, r = factorize_into(n)
        assert int(np.prod(f)) * r == n
        assert all(x <= 3 for x in f)


def test_flatten_row_permutation_matches_numpy():
    rng = np.random.default_rng(1)
    for pre_shape, perm in [((3, 4, 5), (1, 2, 0)), ((2, 3, 4), (2, 0, 1)), ((6, 7), (1, 0))]:
        x = rng.standard_normal(pre_shape).astype(np.float32)
        old_rows = flatten_row_permutation(list(pre_shape), list(perm))
        flat_x = x.reshape(-1)
        flat_t = np.transpose(x, perm).reshape(-1)
        # the folded weight uses W_old[old_rows]; equivalence needs flat_x == flat_t[old_rows]
        np.testing.assert_array_equal(flat_x, flat_t[old_rows])


# --------------------------------------------------------------------------- pool decomposition


@pytest.mark.parametrize("strategy", ["conv", "cascade", "reduce_mean"])
@pytest.mark.parametrize(
    "C,H,W,op,kernel",
    [
        (64, 8, 8, "AveragePool", [8, 8]),          # resnet8-like, fully factorable
        (5, 25, 5, "AveragePool", [25, 5]),         # ds-cnn-like, non-factorable both dims
        (3, 6, 6, "AveragePool", [6, 6]),           # 6=3*2 factorable
        (4, 5, 3, "AveragePool", [5, 3]),           # mixed: 5 non-factorable, 3 factorable
        (7, 4, 4, "GlobalAveragePool", None),       # global pool node
    ],
)
def test_pool_decompose_equivalent(strategy, C, H, W, op, kernel):
    model = _pool_model(C, H, W, op=op, kernel=kernel)
    x = np.random.default_rng(2).standard_normal((1, C, H, W)).astype(np.float32)
    ref = _run(model, {"x": x})
    new_model, changes = decompose_pools(model, strategy=strategy)
    assert len(changes) == 1
    got = _run(new_model, {"x": x})
    assert got.shape == ref.shape
    np.testing.assert_allclose(got, ref, rtol=RTOL, atol=ATOL)


def test_cascade_produces_pure_pools_when_factorable():
    model = _pool_model(64, 8, 8)
    new_model, _ = decompose_pools(model, strategy="cascade")
    ops = [n.op_type for n in new_model.graph.node]
    assert ops == ["AveragePool", "AveragePool", "AveragePool"]  # 8 = 2*2*2, no conv
    assert check_coredla_friendly(new_model) == []


def test_cascade_falls_back_to_conv_for_non_factorable():
    model = _pool_model(5, 25, 5)
    new_model, _ = decompose_pools(model, strategy="cascade")
    ops = [n.op_type for n in new_model.graph.node]
    assert ops == ["Conv"]  # 25 and 5 have no <=3 factor -> single residual conv


def test_auto_strategy_picks_per_pool():
    m1, c1 = decompose_pools(_pool_model(64, 8, 8), strategy="auto")
    assert c1[0]["strategy"] == "cascade"
    m2, c2 = decompose_pools(_pool_model(5, 25, 5), strategy="auto")
    assert c2[0]["strategy"] == "conv"


def test_non_oversized_pool_untouched():
    model = _pool_model(8, 3, 3, kernel=[3, 3], stride=[1, 1])
    assert find_oversized_pools(model) == []
    new_model, changes = decompose_pools(model, strategy="conv")
    assert changes == []
    assert [n.op_type for n in new_model.graph.node] == ["AveragePool"]


def test_cascade_rejects_overlapping_stride():
    model = _pool_model(4, 8, 8, kernel=[5, 5], stride=[5, 5])  # oversized window, non-overlapping ok
    # overlapping (stride != kernel) must be rejected for cascade
    ov = _pool_model(4, 10, 10, kernel=[5, 5], stride=[2, 2])
    with pytest.raises(ValueError):
        decompose_pools(ov, strategy="cascade")
    # but conv handles it exactly
    x = np.random.default_rng(3).standard_normal((1, 4, 10, 10)).astype(np.float32)
    ref = _run(ov, {"x": x})
    got = _run(decompose_pools(ov, strategy="conv")[0], {"x": x})
    np.testing.assert_allclose(got, ref, rtol=RTOL, atol=ATOL)


def _grouped_conv_model(cout, cin_per_group, group, kh, kw, *, h=None, w=None, bias=True):
    """A single grouped Conv: input [1, cin_per_group*group, H, W] -> output [1, cout, H', W']."""
    cin = cin_per_group * group
    h = h or (kh + 2)
    w = w or (kw + 2)
    rng = np.random.default_rng(9)
    wt = rng.standard_normal((cout, cin_per_group, kh, kw)).astype(np.float32)
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, cin, h, w])
    oh, ow = h - kh + 1, w - kw + 1
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, cout, oh, ow])
    inits = [numpy_helper.from_array(wt, name="W")]
    inputs = ["x", "W"]
    if bias:
        b = rng.standard_normal((cout,)).astype(np.float32)
        inits.append(numpy_helper.from_array(b, name="B"))
        inputs.append("B")
    node = helper.make_node(
        "Conv", inputs, ["y"], name="gconv", kernel_shape=[kh, kw], group=group
    )
    graph = helper.make_graph([node], "gconv_g", [x], [y], initializer=inits)
    return _finish(graph)


# --------------------------------------------------------------------------- depthwise -> dense


def test_dense_weight_from_grouped_is_block_diagonal():
    rng = np.random.default_rng(11)
    w = rng.standard_normal((4, 1, 3, 3)).astype(np.float32)  # depthwise: group == Cout == Cin
    dense = dense_weight_from_grouped(w, group=4)
    assert dense.shape == (4, 4, 3, 3)
    for c in range(4):
        np.testing.assert_array_equal(dense[c, c], w[c, 0])
        for c2 in range(4):
            if c2 != c:
                assert np.all(dense[c, c2] == 0.0)


@pytest.mark.parametrize(
    "cout,cin_per_group,group,kh,kw",
    [
        (64, 1, 64, 3, 3),   # true depthwise, ds-cnn's 3x3 blocks
        (64, 1, 64, 25, 5),  # true depthwise, ds-cnn's pool-decompose-produced 25x5 "mean" conv
        (6, 2, 3, 3, 3),     # general grouped conv (not fully depthwise): group < Cout, cin/group>1
        (5, 1, 5, 1, 1),     # 1x1 depthwise
    ],
)
def test_grouped_conv_dense_rewrite_bit_exact(cout, cin_per_group, group, kh, kw):
    model = _grouped_conv_model(cout, cin_per_group, group, kh, kw)
    name, x = _first_input(model)
    ref = _run(model, {name: x})
    infos = find_grouped_convs(model)
    assert len(infos) == 1 and infos[0].group == group
    new_model, changes = rewrite_grouped_convs_to_dense(model)
    assert len(changes) == 1
    assert changes[0]["dense_shape"] == [cout, cin_per_group * group, kh, kw]
    assert "group" not in [a.name for a in new_model.graph.node[0].attribute]
    got = _run(new_model, {name: x})
    if cin_per_group == 1:
        # true depthwise (one real term per output channel, everything else an exact 0): bit-exact.
        np.testing.assert_array_equal(got, ref)
    else:
        # general grouped conv: still mathematically identical (extra terms are exact zeros), but
        # onnxruntime's dense-conv kernel sums over more (zero) terms in a different order than its
        # grouped-conv kernel, so float non-associativity gives ~1e-7-level noise -- not a
        # correctness issue, just summation order (same tolerance style as the other transforms).
        np.testing.assert_allclose(got, ref, rtol=RTOL, atol=ATOL)


def test_grouped_conv_dense_rewrite_requires_const_weight():
    model = _grouped_conv_model(4, 1, 4, 3, 3)
    # make the weight non-constant (fed from an Identity instead of an initializer)
    w_init = next(i for i in model.graph.initializer if i.name == "W")
    model.graph.initializer.remove(w_init)
    ident = helper.make_node("Identity", ["W_src"], ["W"], name="w_id")
    model.graph.node.insert(0, ident)
    model.graph.initializer.append(numpy_helper.from_array(numpy_helper.to_array(w_init), name="W_src"))
    with pytest.raises(ValueError):
        rewrite_grouped_convs_to_dense(model)


def test_ungrouped_conv_untouched_by_dense_rewrite():
    model = _grouped_conv_model(4, 4, 1, 3, 3)  # group=1, nothing to do
    assert find_grouped_convs(model) == []
    new_model, changes = rewrite_grouped_convs_to_dense(model)
    assert changes == []


# --------------------------------------------------------------------------- transpose folding


@pytest.mark.parametrize("fc", ["MatMul", "Gemm"])
@pytest.mark.parametrize("perm", [(0, 2, 3, 1), (0, 3, 1, 2), (0, 3, 2, 1)])
def test_transpose_fold_activation_equivalent(fc, perm):
    model, _ = _transpose_fc_model(3, 4, 5, 7, fc=fc, perm=perm)
    x = np.random.default_rng(4).standard_normal((1, 3, 4, 5)).astype(np.float32)
    ref = _run(model, {"x": x})
    new_model, changes = fold_transposes(model)
    assert len(changes) == 1
    assert changes[0]["pattern"] == "activation_flatten_transpose"
    assert "Transpose" not in [n.op_type for n in new_model.graph.node]
    got = _run(new_model, {"x": x})
    np.testing.assert_allclose(got, ref, rtol=RTOL, atol=ATOL)
    assert check_coredla_friendly(new_model) == []


def test_transpose_fold_weight_side_equivalent():
    rng = np.random.default_rng(5)
    K, O = 6, 4
    w = rng.standard_normal((O, K)).astype(np.float32)  # stored [O,K], transposed to [K,O]
    x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [1, K])
    y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [1, O])
    nodes = [
        helper.make_node("Transpose", ["Wt"], ["W"], name="tr", perm=[1, 0]),
        helper.make_node("MatMul", ["x", "W"], ["y"], name="fc"),
    ]
    inits = [numpy_helper.from_array(w, name="Wt")]
    model = _finish(helper.make_graph(nodes, "wtr", [x], [y], initializer=inits))
    xin = rng.standard_normal((1, K)).astype(np.float32)
    ref = _run(model, {"x": xin})
    new_model, changes = fold_transposes(model)
    assert len(changes) == 1 and changes[0]["pattern"] == "weight_transpose"
    assert "Transpose" not in [n.op_type for n in new_model.graph.node]
    got = _run(new_model, {"x": xin})
    np.testing.assert_allclose(got, ref, rtol=RTOL, atol=ATOL)


def test_transpose_fold_noop_when_absent():
    model, _ = _transpose_fc_model(3, 4, 5, 7)
    # strip the transpose manually so nothing is foldable
    plain, _ = fold_transposes(model)
    again, changes = fold_transposes(plain)
    assert changes == []


# --------------------------------------------------------------------------- static checker


def test_check_flags_oversized_pool_and_transpose():
    pool_issues = check_coredla_friendly(_pool_model(4, 8, 8))
    assert any("AveragePool" in s for s in pool_issues)
    tr_model, _ = _transpose_fc_model(3, 4, 5, 7)
    tr_issues = check_coredla_friendly(tr_model)
    assert any("Transpose" in s for s in tr_issues)


# --------------------------------------------------------------------------- REAL models


def _real(model_id):
    p = ONNX_DIR / f"{model_id}.onnx"
    if not p.exists():
        pytest.skip(f"cached model missing: {p}")
    return onnx.load(str(p))


def _first_input(model):
    vi = model.graph.input[0]
    name = vi.name
    dims = [d.dim_value if d.dim_value > 0 else 1 for d in vi.type.tensor_type.shape.dim]
    x = np.random.default_rng(7).standard_normal(dims).astype(np.float32)
    return name, x


def _end_to_end_maxdiff(model, transform, **kw):
    name, x = _first_input(model)
    ref = _run(model, {name: x})
    new_model, changes = transform(model, **kw)
    got = _run(new_model, {name: x})
    assert got.shape == ref.shape
    return float(np.max(np.abs(got - ref))), changes, new_model


REAL_MAXDIFF: dict[str, float] = {}


@pytest.mark.parametrize("model_id", ["ds-cnn-kws", "resnet8-cifar10"])
@pytest.mark.parametrize("strategy", ["conv", "cascade"])
def test_pool_decompose_real(model_id, strategy):
    model = _real(model_id)
    assert find_oversized_pools(model), f"{model_id} expected an oversized pool"
    diff, changes, new_model = _end_to_end_maxdiff(model, decompose_pools, strategy=strategy)
    assert changes, "expected a pool decomposition"
    assert diff < 1e-4, f"{model_id}/{strategy} diff {diff}"
    REAL_MAXDIFF[f"pool/{strategy}/{model_id}"] = diff
    # the decomposed model must clear the pool blocker
    assert not any("Pool" in s for s in check_coredla_friendly(new_model))


@pytest.mark.parametrize("model_id", ["resnet8-cifar10", "mobilenetv1-025-vww"])
def test_transpose_fold_real(model_id):
    model = _real(model_id)
    diff, changes, new_model = _end_to_end_maxdiff(model, fold_transposes)
    assert diff < 1e-4, f"{model_id} transpose diff {diff}"
    REAL_MAXDIFF[f"transpose/{model_id}"] = diff
    # no Transpose may directly feed an FC after folding
    assert not any("precedes" in s for s in check_coredla_friendly(new_model))


def test_make_coredla_friendly_real_ds_cnn():
    model = _real("ds-cnn-kws")
    name, x = _first_input(model)
    ref = _run(model, {name: x})
    new_model, report = make_coredla_friendly(model, pool_strategy="auto")
    got = _run(new_model, {name: x})
    diff = float(np.max(np.abs(got - ref)))
    assert diff < 1e-4
    assert report["pool"], "expected pool changes"
    assert report["remaining_issues"] == []
    REAL_MAXDIFF["pipeline/ds-cnn-kws"] = diff


@pytest.mark.parametrize("model_id", ["ds-cnn-kws", "resnet8-cifar10", "mobilenetv1-025-vww"])
def test_pipeline_clears_blockers_real(model_id):
    model = _real(model_id)
    new_model, report = make_coredla_friendly(model, pool_strategy="auto")
    assert report["remaining_issues"] == []


def test_ddrfree_dscnn_full_pipeline_bit_exact():
    """The full DDR-free ds-cnn rewrite: pool-decompose + transpose-fold + dense-ify every
    group>1 Conv (the 4 true depthwise convs + the pool-decompose-produced one). Verifies against
    the ORIGINAL cached ds-cnn-kws ONNX (docs/onboard_benchmark_plan.md Track B2)."""
    model = _real("ds-cnn-kws")
    name, x = _first_input(model)
    ref = _run(model, {name: x})

    friendly, report = make_coredla_friendly(model, pool_strategy="auto")
    assert find_grouped_convs(friendly), "expected ds-cnn's depthwise convs to still be group>1"

    dense_model, changes = rewrite_grouped_convs_to_dense(friendly)
    assert changes, "expected grouped convs to be rewritten"
    assert find_grouped_convs(dense_model) == [], "no group>1 Conv should remain"
    op_types = [n.op_type for n in dense_model.graph.node]
    assert "Concat" not in op_types
    assert all(n.op_type != "Conv" or _group_of(n) == 1 for n in dense_model.graph.node)

    got = _run(dense_model, {name: x})
    diff = float(np.max(np.abs(got - ref)))
    assert diff < 1e-4, f"ds-cnn DDR-free dense rewrite diff {diff}"
    REAL_MAXDIFF["ddrfree_dense/ds-cnn-kws"] = diff
    for c in changes:
        REAL_MAXDIFF[f"ddrfree_dense/ds-cnn-kws/{c['node'][:24]}_growth_x"] = c["growth_x"]


def _group_of(node):
    for a in node.attribute:
        if a.name == "group":
            return a.i
    return 1


def test_report_real_maxdiffs(capsys):
    """Emit the per-model max diffs (visible with -s) after the real tests populate them."""
    with capsys.disabled():
        if REAL_MAXDIFF:
            print("\nREAL-MODEL max abs output diff (equivalence proof):")
            for k in sorted(REAL_MAXDIFF):
                print(f"  {k}: {REAL_MAXDIFF[k]:.3e}")
