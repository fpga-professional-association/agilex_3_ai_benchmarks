"""Unit tests for the pure record-packing math (issue #5). No OpenVINO, no datasets."""

import numpy as np
import pytest

import layouts
import packlib


# (name, input_bytes, resident_weight_bytes, expected_stride, expected_records) from
# docs/record_format.md — the packer must reproduce these exactly.
CAPACITY_TABLE = [
    ("AD-ToyADMOS", 640, 0, 704, 23738),
    ("KWS-SpeechCommands", 490, 0, 512, 32640),
    ("CIFAR10-ResNet8", 3072, 0, 3136, 5328),
    ("VWW-MobileNetV1", 27648, 0, 27712, 603),
    ("MobileNetV2-224", 150528, 3538984, 150592, 87),
    ("ResNet18-224", 150528, 11690000, 150592, 33),
    ("TinyYOLOv3-416", 519168, 8860000, 519232, 15),
]


@pytest.mark.parametrize("name,n,w,stride,recs", CAPACITY_TABLE)
def test_stride_matches_table(name, n, w, stride, recs):
    assert packlib.stride_for(n) == stride


@pytest.mark.parametrize("name,n,w,stride,recs", CAPACITY_TABLE)
def test_capacity_matches_table(name, n, w, stride, recs):
    reserve = w if w > packlib.ONCHIP_W_LIMIT else 0
    assert packlib.max_records(stride, reserve_weights=reserve) == recs


def test_stride_is_burst_aligned_and_fits_label():
    for n in [0, 1, 63, 64, 65, 490, 3072, 150528]:
        s = packlib.stride_for(n)
        assert s % packlib.BURST_ALIGN == 0
        assert s >= n + 1


def test_quantize_matches_hand_computed():
    # scale 0.5, zp 0: x/0.5 = 2x, round-half-to-even, clip [-128,127]
    x = np.array([0.0, 0.24, 0.25, 0.75, -0.25, 100.0, -100.0], dtype=np.float32)
    q = packlib.quantize_int8(x, scale=0.5, zero_point=0)
    # 0->0, 0.24->0 (0.48->0), 0.25->0 (0.5 rounds to even 0), 0.75->2 (1.5->2),
    # -0.25->0 (-0.5-> even 0), 100->127 (clip), -100->-128 (clip)
    assert q.tolist() == [0, 0, 0, 2, 0, 127, -128]
    assert q.dtype == np.int8


def test_quantize_zero_point_and_passthrough():
    q = packlib.quantize_int8(np.array([1.0, 2.0]), scale=1.0, zero_point=5)
    assert q.tolist() == [6, 7]
    already = np.array([-3, 7], dtype=np.int8)
    assert packlib.quantize_int8(already, scale=999.0, zero_point=42) is already  # untouched


def test_build_record_layout_and_pad():
    body = bytes([1, 2, 3])          # N = 3
    stride = packlib.stride_for(3)   # 64
    rec = packlib.build_record(body, label=7, stride=stride)
    assert len(rec) == 64
    assert rec[:3] == body
    assert rec[3] == 7               # label at offset N
    assert all(b == 0 for b in rec[4:])  # zero pad
    assert packlib.label_at(rec, 3) == 7


def test_build_record_rejects_wrong_stride_and_bad_label():
    with pytest.raises(ValueError):
        packlib.build_record(b"\x01\x02", label=0, stride=999)   # 999 not a valid stride
    with pytest.raises(ValueError):
        packlib.build_record(b"\x01", label=300, stride=packlib.stride_for(1))


def test_layouts_nhwc_transpose_and_raw():
    # (C=2, H=1, W=3) int8 tensor
    t = np.arange(6, dtype=np.int8).reshape(2, 1, 3)
    assert layouts.transform("raw", t) == t.tobytes()
    assert layouts.transform("nchw", t) == t.tobytes()
    # nhwc -> (H,W,C): element order interleaves channels
    expected = np.transpose(t, (1, 2, 0)).tobytes()
    assert layouts.transform("nhwc", t) == expected
    assert layouts.transform("nhwc", t) != layouts.transform("raw", t)


def test_layouts_reject_bad_dtype_and_shape():
    with pytest.raises(ValueError):
        layouts.transform("raw", np.array([1, 2], dtype=np.float32))
    with pytest.raises(ValueError):
        layouts.transform("nhwc", np.arange(6, dtype=np.int8))  # not 3-D
    with pytest.raises(KeyError):
        layouts.transform("bogus", np.arange(6, dtype=np.int8).reshape(2, 1, 3))
