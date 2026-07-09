"""Make a CoreDLA-rejected ONNX model compilable without changing its mathematics.

Issue #14 (equivalence-preserving graph surgery). This is the top-level entry point the per-model
agents call: it loads an ONNX, detects which equivalence-preserving transforms apply (oversized
pool decomposition, Transpose->FC folding), applies them, writes a transformed ONNX, and reports
what changed. A static checker verifies the result no longer trips the two known CoreDLA blockers.

CLI::

    python -m graph_ops.coredla_friendly IN.onnx OUT.onnx [--pool-strategy auto|conv|cascade|reduce_mean]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import onnx

from .pool_decompose import MAX_STRIDE, MAX_WINDOW, decompose_pools, factorize_into
from .transpose_fold import _attr_ints, fold_transposes


def make_coredla_friendly(
    model: onnx.ModelProto,
    *,
    pool_strategy: str = "auto",
    do_pool: bool = True,
    do_transpose: bool = True,
) -> tuple[onnx.ModelProto, dict]:
    """Apply every applicable transform to ``model``; return ``(new_model, report)``.

    ``report`` has keys ``pool`` and ``transpose`` (lists of per-change dicts) and ``remaining_issues``
    (from :func:`check_coredla_friendly`). The input ``model`` is not mutated.
    """
    report: dict = {"pool": [], "transpose": []}
    if do_pool:
        model, pool_changes = decompose_pools(model, strategy=pool_strategy)
        report["pool"] = pool_changes
    if do_transpose:
        model, tr_changes = fold_transposes(model)
        report["transpose"] = tr_changes
    report["remaining_issues"] = check_coredla_friendly(model)
    return model, report


def check_coredla_friendly(
    model: onnx.ModelProto, *, max_window: int = MAX_WINDOW, max_stride: int = MAX_STRIDE
) -> list[str]:
    """Return a list of remaining CoreDLA blockers (empty == clean).

    Flags (1) any AveragePool/GlobalAveragePool whose window exceeds ``max_window`` or stride exceeds
    ``max_stride`` (GlobalAveragePool over a >window spatial extent counts), and (2) any Transpose
    whose output feeds a Gemm/MatMul either directly or through a single Reshape/Flatten.
    """
    from onnx import shape_inference

    issues: list[str] = []
    try:
        inferred = shape_inference.infer_shapes(model)
    except Exception:
        inferred = model
    shapes = {}
    for vi in list(inferred.graph.value_info) + list(inferred.graph.input):
        dims = [
            d.dim_value if d.HasField("dim_value") else d.dim_param
            for d in vi.type.tensor_type.shape.dim
        ]
        shapes[vi.name] = dims

    producers = {o: n for n in model.graph.node for o in n.output}

    for node in model.graph.node:
        if node.op_type == "AveragePool":
            ks = _attr_ints(node, "kernel_shape") or []
            strides = _attr_ints(node, "strides") or [1] * len(ks)
            if any(k > max_window for k in ks) or any(s > max_stride for s in strides):
                issues.append(
                    f"AveragePool {node.name!r} window={ks} stride={strides} exceeds "
                    f"{max_window}/{max_stride}"
                )
        elif node.op_type == "GlobalAveragePool":
            in_shape = shapes.get(node.input[0])
            if in_shape and len(in_shape) == 4:
                _, _, h, w = in_shape
                if (isinstance(h, int) and h > max_window) or (isinstance(w, int) and w > max_window):
                    issues.append(
                        f"GlobalAveragePool {node.name!r} over {h}x{w} exceeds window {max_window}"
                    )
        elif node.op_type in ("MatMul", "Gemm"):
            for name in node.input:
                p = producers.get(name)
                if p is None:
                    continue
                if p.op_type == "Transpose":
                    issues.append(
                        f"Transpose {p.name!r} directly precedes {node.op_type} {node.name!r}"
                    )
                elif p.op_type in ("Reshape", "Flatten"):
                    pp = producers.get(p.input[0])
                    if pp is not None and pp.op_type == "Transpose":
                        issues.append(
                            f"Transpose {pp.name!r} precedes {node.op_type} {node.name!r} via "
                            f"{p.op_type} {p.name!r}"
                        )
    return issues


def transform_file(
    in_path: Path, out_path: Path, *, pool_strategy: str = "auto"
) -> dict:
    """Load ``in_path``, transform, save to ``out_path``; return the change report."""
    model = onnx.load(str(in_path))
    new_model, report = make_coredla_friendly(model, pool_strategy=pool_strategy)
    onnx.checker.check_model(new_model)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(new_model, str(out_path))
    report["input"] = str(in_path)
    report["output"] = str(out_path)
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input", type=Path, help="input ONNX")
    ap.add_argument("output", type=Path, help="output (transformed) ONNX")
    ap.add_argument(
        "--pool-strategy",
        default="auto",
        choices=["auto", "conv", "cascade", "reduce_mean"],
        help="how to decompose oversized pools (default: auto)",
    )
    args = ap.parse_args(argv)
    report = transform_file(args.input, args.output, pool_strategy=args.pool_strategy)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    if report["remaining_issues"]:
        print("WARNING: remaining CoreDLA blockers:", file=sys.stderr)
        for issue in report["remaining_issues"]:
            print("  -", issue, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
