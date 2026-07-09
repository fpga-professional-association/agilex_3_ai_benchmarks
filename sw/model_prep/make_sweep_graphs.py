#!/usr/bin/env python3
"""Generate a synthetic conv-stack ONNX sweep spanning ~3 decades of MACs/inference (issue #20).

PLAN §7 L4: sequenced overlays carry a fixed per-inference cost (descriptor setup, config, pipeline
drain). To isolate it, `sw/host/fit_l4.py` fits `time_per_inference = overhead + MACs / rate`
against a family of graphs that vary only in size. This module builds that family: same layer type
(3x3 Conv2D + ReLU) repeated `depth` times over a fixed small input, with the channel width per
depth chosen so the *family* spans the target MACs range even though depth alone (1/2/4/8/16, a 16x
range) would not reach 3 decades on its own -- see `solve_channels_for_macs`.

This is the SOFTWARE half of #20 (buildable ahead of hardware per the issue). It only produces ONNX
graphs + a manifest of their analytic MACs/params; quantizing them (the #3 pipeline) and compiling +
running them under method A on silicon is the "## Hardware handoff" in the #20 PR.

Usage:
    python make_sweep_graphs.py --out-dir models/onnx/l4_sweep
    python make_sweep_graphs.py --dry-run          # print the manifest, skip writing .onnx files
                                                     # (works even without the onnx package)

Writes `<out-dir>/l4_sweep_d<depth>.onnx` per point plus `<out-dir>/manifest.json` (depth, channels,
spatial, kernel, macs, params per point) that `fit_l4.py` / the hardware run harness key off of.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import common  # sw/model_prep/common.py (issue #2): REPO_ROOT/ONNX_DIR, tool_versions()

REPO_ROOT = common.REPO_ROOT
DEFAULT_OUT_DIR = common.ONNX_DIR / "l4_sweep"
DEFAULT_DEPTHS = (1, 2, 4, 8, 16)          # PLAN §7 L4 / issue #20 deliverable
DEFAULT_SPATIAL = 16                        # "fixed small input"
DEFAULT_KERNEL = 3
DEFAULT_IN_CHANNELS = 1
DEFAULT_MACS_MIN = 50_000                   # issue #20: "e.g. 50 K -> 50 M"
DEFAULT_MACS_MAX = 80_000_000               # headroom over 50M: integer channel rounding at each
                                             # depth shaves a bit off the target span, so aim past
                                             # 3 decades rather than exactly at it (see --min-decades)


# --------------------------------------------------------------------------------------------
# Pure MACs math -- no onnx import needed, testable without the package installed (AGENTS.md:
# pure-logic parts must be testable without heavy tool deps).
# --------------------------------------------------------------------------------------------

def conv_stack_macs(depth: int, channels: int, spatial: int, kernel: int,
                     in_channels: int = DEFAULT_IN_CHANNELS) -> int:
    """Exact MACs for `depth` stacked same-padding/stride-1 Conv2D(kernel) + ReLU layers.

    Layer 0 is in_channels -> channels; layers 1..depth-1 are channels -> channels. Every layer
    keeps the same `spatial` x `spatial` feature map (same-padding, stride 1), matching the "same
    layer type ... fixed small input" sweep design in issue #20.
    """
    if depth < 1:
        raise ValueError(f"depth must be >= 1, got {depth}")
    if channels < 1 or spatial < 1 or kernel < 1 or in_channels < 1:
        raise ValueError("channels, spatial, kernel, in_channels must all be >= 1")
    hwk2 = spatial * spatial * kernel * kernel
    first = hwk2 * in_channels * channels
    rest = hwk2 * channels * channels * (depth - 1)
    return first + rest


def conv_stack_params(depth: int, channels: int, kernel: int,
                       in_channels: int = DEFAULT_IN_CHANNELS) -> int:
    """Weight + bias element count for the same stack (informational; not used in the MACs fit)."""
    first = kernel * kernel * in_channels * channels + channels
    rest = (kernel * kernel * channels * channels + channels) * (depth - 1)
    return first + rest


def solve_channels_for_macs(depth: int, target_macs: float, spatial: int, kernel: int,
                             in_channels: int = DEFAULT_IN_CHANNELS) -> int:
    """Smallest channel count (>=1) whose exact MACs are closest to `target_macs`.

    `conv_stack_macs` is linear in channels for depth==1 and quadratic for depth>1 (the
    channels->channels layers dominate); solve the corresponding equation for a real root, then
    round to the nearest integer >=1 and let the caller re-derive the *actual* MACs from the
    rounded channel count (the fit only needs the family to span decades, not to hit exact targets).
    """
    if target_macs <= 0:
        raise ValueError("target_macs must be positive")
    hwk2 = spatial * spatial * kernel * kernel
    if depth == 1:
        c = target_macs / (hwk2 * in_channels)
    else:
        a = hwk2 * (depth - 1)
        b = hwk2 * in_channels
        c_term = -target_macs
        disc = b * b - 4 * a * c_term
        c = (-b + math.sqrt(disc)) / (2 * a)
    return max(1, round(c))


@dataclass(frozen=True)
class SweepPoint:
    point_id: str
    depth: int
    channels: int
    spatial: int
    kernel: int
    in_channels: int
    macs: int
    params: int
    onnx_path: str | None = None


def default_sweep_points(depths: tuple[int, ...] = DEFAULT_DEPTHS,
                          spatial: int = DEFAULT_SPATIAL, kernel: int = DEFAULT_KERNEL,
                          in_channels: int = DEFAULT_IN_CHANNELS,
                          macs_min: float = DEFAULT_MACS_MIN,
                          macs_max: float = DEFAULT_MACS_MAX) -> list[SweepPoint]:
    """Build the sweep family: one point per depth, channels chosen so MACs span [macs_min, macs_max].

    Targets are log-spaced across `depths` (so the smallest depth lands near macs_min and the
    largest near macs_max), then each depth's channel count is solved to approximate its target.
    Growing both depth AND width together is what gets a 16x depth range (1->16) up to the required
    >=3 decades of MACs (issue #20 acceptance criteria) -- depth alone only spans ~1.2 decades.
    """
    if len(depths) < 2:
        raise ValueError("need at least 2 depths to define a MACs span")
    n = len(depths)
    log_min, log_max = math.log10(macs_min), math.log10(macs_max)
    points = []
    for i, depth in enumerate(sorted(depths)):
        frac = i / (n - 1)
        target = 10 ** (log_min + frac * (log_max - log_min))
        channels = solve_channels_for_macs(depth, target, spatial, kernel, in_channels)
        macs = conv_stack_macs(depth, channels, spatial, kernel, in_channels)
        params = conv_stack_params(depth, channels, kernel, in_channels)
        points.append(SweepPoint(
            point_id=f"l4-sweep-d{depth}",
            depth=depth, channels=channels, spatial=spatial, kernel=kernel,
            in_channels=in_channels, macs=macs, params=params,
        ))
    return points


def macs_span_decades(points: list[SweepPoint]) -> float:
    macs = [p.macs for p in points]
    return math.log10(max(macs) / min(macs))


# --------------------------------------------------------------------------------------------
# ONNX graph construction -- needs the `onnx` package (sw/model_prep/requirements.txt).
# --------------------------------------------------------------------------------------------

def build_onnx_model(point: SweepPoint):
    """Build the ModelProto for one sweep point: Conv(k)+ReLU x depth -> GAP -> Flatten.

    Weight/bias initializers are deterministic (numpy fixed-seed, keyed on point_id) fp32 values --
    plausible enough to survive the #3 NNCF PTQ pipeline; the sweep is about graph *shape* (MACs),
    not learned weights, so there is no training/calibration-accuracy claim here.
    """
    try:
        import numpy as np
        import onnx
        from onnx import TensorProto, helper, numpy_helper
    except ImportError as exc:
        raise RuntimeError(
            "the onnx package is required to build .onnx files "
            "(pip install -r sw/model_prep/requirements.txt); "
            "pure MACs math (conv_stack_macs / default_sweep_points) does not need it"
        ) from exc

    rng = np.random.default_rng(seed=hash(point.point_id) & 0xFFFFFFFF)
    nodes, initializers = [], []
    in_name = "input"
    cur_channels = point.in_channels
    for layer in range(point.depth):
        out_channels = point.channels
        w_name, b_name = f"conv{layer}.weight", f"conv{layer}.bias"
        w = rng.normal(scale=0.05, size=(out_channels, cur_channels, point.kernel, point.kernel)).astype("float32")
        b = np.zeros((out_channels,), dtype="float32")
        initializers.append(numpy_helper.from_array(w, name=w_name))
        initializers.append(numpy_helper.from_array(b, name=b_name))
        conv_out = f"conv{layer}_out"
        pad = point.kernel // 2
        nodes.append(helper.make_node(
            "Conv", inputs=[in_name, w_name, b_name], outputs=[conv_out], name=f"Conv{layer}",
            kernel_shape=[point.kernel, point.kernel], pads=[pad, pad, pad, pad], strides=[1, 1],
        ))
        relu_out = f"relu{layer}_out"
        nodes.append(helper.make_node("Relu", inputs=[conv_out], outputs=[relu_out], name=f"Relu{layer}"))
        in_name = relu_out
        cur_channels = out_channels

    nodes.append(helper.make_node("GlobalAveragePool", inputs=[in_name], outputs=["gap_out"], name="GAP"))
    nodes.append(helper.make_node("Flatten", inputs=["gap_out"], outputs=["output"], name="Flatten", axis=1))

    graph_input = helper.make_tensor_value_info(
        "input", TensorProto.FLOAT, [1, point.in_channels, point.spatial, point.spatial])
    graph_output = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, point.channels])

    graph = helper.make_graph(nodes, point.point_id, [graph_input], [graph_output], initializers)
    model = helper.make_model(graph, producer_name="agilex3-l4-sweep", opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8  # OpenVINO 2024.6 / onnx opset-13 compatible (PLAN §9 PH2 tool line)
    onnx.checker.check_model(model)
    return model


def write_sweep(points: list[SweepPoint], out_dir: Path, dry_run: bool = False) -> list[SweepPoint]:
    """Write one .onnx per point (unless dry_run) and return points with onnx_path filled in."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for p in points:
        rel_path = f"{p.point_id}.onnx"
        if not dry_run:
            model = build_onnx_model(p)
            import onnx
            onnx.save(model, str(out_dir / rel_path))
        written.append(SweepPoint(**{**asdict(p), "onnx_path": rel_path}))
    return written


def write_manifest(points: list[SweepPoint], out_dir: Path) -> Path:
    manifest_path = out_dir / "manifest.json"
    manifest = {
        "generator": "sw/model_prep/make_sweep_graphs.py",
        "plan_ref": "§7 L4",
        "macs_span_decades": round(macs_span_decades(points), 3),
        "tool_versions": common.tool_versions("onnx", "numpy"),
        "points": [asdict(p) for p in points],
        "notes": (
            "Issue #20 step 1: before compiling every point, sanity-check the smallest is "
            "overhead-dominated and the largest is compute-dominated with the #6 performance "
            "estimator (scripts/estimate.py --fanalyze-performance) against models/arch/"
            "AGX3_Performance.arch. Issue #20 step 5 / PLAN §7 L4 'repeat': once this sweep runs "
            "under method A on silicon (#18), repeat under the Spatial Compiler per issue #24 -- "
            "not done here, hardware+toolchain gated."
        ),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    ap.add_argument("--depths", default=",".join(str(d) for d in DEFAULT_DEPTHS),
                     help="comma-separated depths, e.g. 1,2,4,8,16")
    ap.add_argument("--spatial", type=int, default=DEFAULT_SPATIAL)
    ap.add_argument("--kernel", type=int, default=DEFAULT_KERNEL)
    ap.add_argument("--in-channels", type=int, default=DEFAULT_IN_CHANNELS)
    ap.add_argument("--macs-min", type=float, default=DEFAULT_MACS_MIN)
    ap.add_argument("--macs-max", type=float, default=DEFAULT_MACS_MAX)
    ap.add_argument("--min-decades", type=float, default=3.0,
                     help="fail if the resulting sweep spans fewer decades than this")
    ap.add_argument("--dry-run", action="store_true",
                     help="print the manifest and skip writing .onnx files (no onnx package needed)")
    args = ap.parse_args(argv)

    depths = tuple(int(d) for d in args.depths.split(","))
    points = default_sweep_points(depths=depths, spatial=args.spatial, kernel=args.kernel,
                                   in_channels=args.in_channels, macs_min=args.macs_min,
                                   macs_max=args.macs_max)

    span = macs_span_decades(points)
    if span < args.min_decades:
        print(f"sweep only spans {span:.2f} decades (< --min-decades {args.min_decades}); "
              f"widen --macs-min/--macs-max or add depths", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    try:
        points = write_sweep(points, out_dir, dry_run=args.dry_run)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    manifest_path = write_manifest(points, out_dir)

    for p in points:
        print(f"{p.point_id}: depth={p.depth} channels={p.channels} macs={p.macs:,} params={p.params:,}")
    print(f"span: {span:.2f} decades")
    print(f"wrote manifest: {manifest_path}" + (" (dry-run: no .onnx files written)" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
