"""Equivalence-preserving ONNX graph surgery for CoreDLA (issue #14).

Small, well-tested transforms that turn CoreDLA-rejected models into compilable ones **without
changing their mathematics** (MLPerf Tiny Closed-Division legal). Each transform preserves
floating-point outputs to ~1e-5; the tests in ``sw/model_prep/tests/test_graph_ops.py`` are the
equivalence proof.

Public API::

    from graph_ops import (
        decompose_pools,              # oversized Average/GlobalAveragePool -> conv / pool-cascade / reduce-mean
        find_oversized_pools,
        fold_transposes,              # Transpose feeding a Gemm/MatMul -> folded into the FC weight
        rewrite_grouped_convs_to_dense,  # group>1 Conv -> block-diagonal group=1 dense Conv (DDR-free)
        find_grouped_convs,
        make_coredla_friendly,        # detect + apply everything, return (model, report)
        check_coredla_friendly,       # static check: any oversized pool or Transpose-before-FC left?
        transform_file,               # file-in/file-out convenience wrapper
    )

``rewrite_grouped_convs_to_dense`` is a standalone step, not part of ``make_coredla_friendly``'s
default pipeline: it is only needed by the DDR-free path (grouped Conv is fine DDR-backed) and its
dense weight is strictly bigger, so it should be opted into deliberately, after the other transforms
have run (see ``docs/onboard_benchmark_plan.md`` Track B2 / ``docs/dscnn_ddrfree_findings.md``).
"""

from .coredla_friendly import (
    check_coredla_friendly,
    make_coredla_friendly,
    transform_file,
)
from .depthwise_to_dense import (
    GroupedConvInfo,
    dense_weight_from_grouped,
    find_grouped_convs,
    rewrite_grouped_convs_to_dense,
)
from .pool_decompose import (
    MAX_STRIDE,
    MAX_WINDOW,
    decompose_pools,
    factorize_into,
    find_oversized_pools,
)
from .transpose_fold import flatten_row_permutation, fold_transposes

__all__ = [
    "decompose_pools",
    "find_oversized_pools",
    "factorize_into",
    "fold_transposes",
    "flatten_row_permutation",
    "make_coredla_friendly",
    "check_coredla_friendly",
    "transform_file",
    "MAX_WINDOW",
    "MAX_STRIDE",
    "GroupedConvInfo",
    "dense_weight_from_grouped",
    "find_grouped_convs",
    "rewrite_grouped_convs_to_dense",
]
