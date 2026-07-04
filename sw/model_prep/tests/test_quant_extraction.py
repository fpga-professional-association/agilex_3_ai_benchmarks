"""Round-trip check: common.signed_int8_affine_params vs the real packer + the FakeQuantize math
it's derived from (issue #3 step 5 / acceptance criterion) -- pure numpy, no OpenVINO needed.

Real quantizers (OpenVINO/NNCF included) "nudge" a calibrated (low, high) range so that the real
value 0.0 lands exactly on an integer code -- otherwise the affine grid isn't zero-point
representable at all (a well-known requirement, e.g. TensorFlow's
``fake_quant_with_min_max_vars``). ``signed_int8_affine_params`` assumes that invariant, same as
every other integer-zero-point quantization scheme; these tests generate *nudged* ranges (as a
real quantizer would emit) rather than arbitrary ones, then check round-trip fidelity against both
the raw FakeQuantize formula and the actual project packer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

import common

PACKER_DIR = Path(__file__).resolve().parent.parent.parent / "packer"


def _import_packlib():
    if str(PACKER_DIR) not in sys.path:
        sys.path.insert(0, str(PACKER_DIR))
    import packlib
    return packlib


def _nudge(low: float, high: float, levels: int = 256) -> tuple[float, float]:
    """Adjust (low, high) so 0.0 lands exactly on an integer code (TF fake-quant nudging)."""
    scale = (high - low) / (levels - 1)
    zero_point_float = -low / scale
    zero_point_nudged = round(min(max(zero_point_float, 0), levels - 1))
    nudged_low = -zero_point_nudged * scale
    nudged_high = (levels - 1 - zero_point_nudged) * scale
    return nudged_low, nudged_high


def _fakequantize_dequant(x: np.ndarray, low: float, high: float, levels: int) -> np.ndarray:
    """OpenVINO's own FakeQuantize formula (quantize-then-dequantize simulation)."""
    scale = (high - low) / (levels - 1)
    level_idx = np.rint((np.clip(x, low, high) - low) / scale)
    return level_idx * scale + low


@pytest.mark.parametrize("raw_low,raw_high", [(0.0, 239.44), (-1.0, 1.0), (0.0, 1.0), (-3.5, 6.25)])
def test_signed_int8_roundtrip_matches_fakequantize(raw_low, raw_high):
    packlib = _import_packlib()
    low, high = _nudge(raw_low, raw_high)
    scale, zero_point = common.signed_int8_affine_params(low, high, levels=256)

    x = np.linspace(low, high, 257)  # dense sample across the full FakeQuantize range
    q = packlib.quantize_int8(x, scale, zero_point)
    dequant_via_packer = (q.astype(np.float64) - zero_point) * scale
    dequant_via_fakequantize = _fakequantize_dequant(x, low, high, levels=256)

    # atol, not exact equality: both sides recompute `scale = (high-low)/(levels-1)`
    # independently, and IEEE754 doesn't guarantee bit-identical results for the same formula
    # evaluated via two separate call sites -- residual is float64 epsilon (~1e-16), nine orders
    # of magnitude below any quantity these scales (~0.01-1) actually distinguish.
    np.testing.assert_allclose(dequant_via_packer, dequant_via_fakequantize, atol=1e-9, rtol=0)


def test_signed_int8_endpoints_map_to_full_range():
    scale, zero_point = common.signed_int8_affine_params(0.0, 239.44, levels=256)
    packlib = _import_packlib()
    assert int(packlib.quantize_int8(np.array([0.0]), scale, zero_point)[0]) == -128
    assert int(packlib.quantize_int8(np.array([239.44]), scale, zero_point)[0]) == 127


def test_fakequantize_params_at_input_rejects_unquantized_ir(tmp_path):
    pytest.importorskip("openvino")
    with pytest.raises(Exception):
        common.fakequantize_params_at_input(tmp_path / "does-not-exist.xml")


def test_roundtrip_against_a_real_quantized_ir_if_present():
    """Strongest check: the actual resnet8 INT8 IR this pipeline generated, if it's on disk."""
    pytest.importorskip("openvino")
    ir_path = common.IR_DIR / "resnet8-cifar10" / "int8" / "resnet8-cifar10.xml"
    if not ir_path.exists():
        pytest.skip("no generated INT8 IR on disk (run quantize_int8.py first)")

    packlib = _import_packlib()
    fq = common.fakequantize_params_at_input(ir_path)
    scale, zero_point = common.signed_int8_affine_params(fq["input_low"], fq["input_high"], int(fq["levels"]))

    x = np.linspace(fq["input_low"], fq["input_high"], 257)
    q = packlib.quantize_int8(x, scale, zero_point)
    dequant_via_packer = (q.astype(np.float64) - zero_point) * scale
    dequant_via_fakequantize = _fakequantize_dequant(x, fq["input_low"], fq["input_high"], int(fq["levels"]))
    np.testing.assert_allclose(dequant_via_packer, dequant_via_fakequantize, atol=1e-9, rtol=0)
