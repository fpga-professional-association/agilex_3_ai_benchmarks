"""Tests for the L4 conv-stack sweep generator (issue #20).

MACs math + sweep-point selection are pure logic and run with no third-party deps. The actual
`.onnx` file construction needs the `onnx` package (sw/model_prep/requirements.txt, heavy per
AGENTS.md) and is skipped in CI via `pytest.importorskip`, same convention as test_common.py.
"""

from __future__ import annotations

import json
import math

import pytest

import make_sweep_graphs as m


# --------------------------------------------------------------------------------------------
# Pure MACs math
# --------------------------------------------------------------------------------------------

def test_conv_stack_macs_single_layer_matches_hand_calc():
    # 1 layer, spatial=4, kernel=3, in_channels=1, channels=2:
    # MACs = H*W*k*k*in_ch*out_ch = 4*4*3*3*1*2 = 288
    assert m.conv_stack_macs(depth=1, channels=2, spatial=4, kernel=3, in_channels=1) == 288


def test_conv_stack_macs_multi_layer_matches_hand_calc():
    # depth=3, spatial=2, kernel=1, in_channels=1, channels=4:
    # layer0: 2*2*1*1*1*4 = 16
    # layer1,2: 2*2*1*1*4*4 = 64 each -> 128
    # total = 144
    assert m.conv_stack_macs(depth=3, channels=4, spatial=2, kernel=1, in_channels=1) == 144


def test_conv_stack_macs_rejects_bad_shapes():
    with pytest.raises(ValueError):
        m.conv_stack_macs(depth=0, channels=1, spatial=1, kernel=1)
    with pytest.raises(ValueError):
        m.conv_stack_macs(depth=1, channels=0, spatial=1, kernel=1)


def test_conv_stack_macs_monotonic_in_depth_and_channels():
    base = m.conv_stack_macs(depth=2, channels=8, spatial=16, kernel=3)
    deeper = m.conv_stack_macs(depth=4, channels=8, spatial=16, kernel=3)
    wider = m.conv_stack_macs(depth=2, channels=16, spatial=16, kernel=3)
    assert deeper > base
    assert wider > base


def test_solve_channels_for_macs_recovers_target_within_rounding():
    for depth in (1, 2, 4, 8, 16):
        target = 1_000_000
        c = m.solve_channels_for_macs(depth, target, spatial=16, kernel=3)
        assert c >= 1
        actual = m.conv_stack_macs(depth, c, spatial=16, kernel=3)
        # integer channel rounding: within 1 channel-step of the target, generously bounded
        assert 0.5 * target <= actual <= 2.0 * target


def test_solve_channels_for_macs_rejects_nonpositive_target():
    with pytest.raises(ValueError):
        m.solve_channels_for_macs(depth=1, target_macs=0, spatial=16, kernel=3)


# --------------------------------------------------------------------------------------------
# Sweep-point selection (default_sweep_points / macs_span_decades)
# --------------------------------------------------------------------------------------------

def test_default_sweep_points_has_five_points_at_the_named_depths():
    points = m.default_sweep_points()
    assert len(points) == 5
    assert [p.depth for p in points] == [1, 2, 4, 8, 16]


def test_default_sweep_points_spans_at_least_three_decades():
    points = m.default_sweep_points()
    span = m.macs_span_decades(points)
    assert span >= 3.0, f"sweep only spans {span:.2f} decades; issue #20 requires >=3"


def test_default_sweep_points_is_monotonically_increasing_in_macs():
    points = m.default_sweep_points()
    macs = [p.macs for p in points]
    assert macs == sorted(macs)
    assert len(set(macs)) == len(macs)  # no duplicate MACs points


def test_default_sweep_points_smallest_near_lower_bound_largest_near_upper_bound():
    points = m.default_sweep_points(macs_min=50_000, macs_max=80_000_000)
    assert points[0].macs < 200_000          # smallest point stays overhead-scale, not compute-scale
    assert points[-1].macs > 10_000_000       # largest point is solidly compute-dominated


def test_default_sweep_points_deterministic():
    a = m.default_sweep_points()
    b = m.default_sweep_points()
    assert [p.macs for p in a] == [p.macs for p in b]
    assert [p.channels for p in a] == [p.channels for p in b]


def test_default_sweep_points_rejects_single_depth():
    with pytest.raises(ValueError):
        m.default_sweep_points(depths=(4,))


def test_macs_span_decades_matches_log10_ratio():
    points = m.default_sweep_points()
    expected = math.log10(points[-1].macs / points[0].macs)
    assert m.macs_span_decades(points) == pytest.approx(expected)


# --------------------------------------------------------------------------------------------
# CLI (dry-run: manifest only, no onnx package needed)
# --------------------------------------------------------------------------------------------

def test_cli_dry_run_writes_manifest_without_onnx_files(tmp_path, monkeypatch):
    out_dir = tmp_path / "l4_sweep"
    rc = m.main(["--out-dir", str(out_dir), "--dry-run"])
    assert rc == 0
    manifest_path = out_dir / "manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text())
    assert len(manifest["points"]) == 5
    assert manifest["macs_span_decades"] >= 3.0
    assert not list(out_dir.glob("*.onnx"))  # dry-run: no .onnx files


def test_cli_fails_loudly_when_span_too_narrow(tmp_path):
    out_dir = tmp_path / "l4_sweep_narrow"
    rc = m.main(["--out-dir", str(out_dir), "--dry-run",
                 "--macs-min", "1000000", "--macs-max", "2000000"])
    assert rc != 0
    assert not (out_dir / "manifest.json").exists()


# --------------------------------------------------------------------------------------------
# ONNX construction (needs the onnx package)
# --------------------------------------------------------------------------------------------

def test_build_onnx_model_without_onnx_raises_clear_error(monkeypatch):
    """When onnx truly isn't importable, build_onnx_model must fail loudly, not silently."""
    if _onnx_available():
        pytest.skip("onnx is installed in this environment; covered by the positive-path test below")
    point = m.default_sweep_points()[0]
    with pytest.raises(RuntimeError, match="onnx"):
        m.build_onnx_model(point)


def _onnx_available() -> bool:
    try:
        import onnx  # noqa: F401
        return True
    except ImportError:
        return False


def test_build_and_write_sweep_produces_valid_checked_onnx(tmp_path):
    pytest.importorskip("onnx")
    import onnx

    points = m.default_sweep_points(depths=(1, 2))  # keep it small/fast
    written = m.write_sweep(points, tmp_path)
    assert len(written) == 2
    for p in written:
        path = tmp_path / p.onnx_path
        assert path.exists()
        model = onnx.load(str(path))
        onnx.checker.check_model(model)  # build_onnx_model already does this; re-check on disk
        assert model.graph.input[0].type.tensor_type.shape.dim[1].dim_value == p.in_channels
        assert model.graph.input[0].type.tensor_type.shape.dim[2].dim_value == p.spatial


def test_onnx_param_count_matches_analytic_formula(tmp_path):
    pytest.importorskip("onnx")
    import common as _common  # reuse the project's own initializer-counting helper

    point = m.default_sweep_points(depths=(1, 4))[1]
    written = m.write_sweep([point], tmp_path)[0]
    onnx_params = _common.param_count_from_onnx(tmp_path / written.onnx_path)
    assert onnx_params == point.params
