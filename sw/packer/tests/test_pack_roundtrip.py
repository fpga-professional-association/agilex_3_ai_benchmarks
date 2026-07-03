"""End-to-end packer tests: pack -> inspect/verify -> decode round-trip, determinism, guards (issue #5)."""

import numpy as np
import pytest

import inspect_recimg
import pack_records
import packlib


def _synthetic(n_samples, shape, seed=0):
    rng = np.random.default_rng(seed)
    inputs = rng.integers(-128, 128, size=(n_samples, *shape), dtype=np.int8)
    labels = rng.integers(0, 10, size=n_samples, dtype=np.int64)
    return inputs, labels


def test_pack_then_verify_clean():
    inputs, labels = _synthetic(50, (3, 8, 8))
    image, manifest = pack_records.pack(inputs, labels, scale=1.0, zero_point=0, layout="raw",
                                        dataset="synthetic", split="test")
    assert manifest["record_count"] == 50
    assert manifest["stride"] == packlib.stride_for(3 * 8 * 8)
    assert inspect_recimg.verify(image, manifest) == []


def test_roundtrip_decode_equals_source_bytes():
    inputs, labels = _synthetic(20, (3, 4, 5), seed=7)
    image, manifest = pack_records.pack(inputs, labels, scale=1.0, zero_point=0, layout="nhwc")
    for k in range(20):
        tensor_bytes, label = inspect_recimg.decode(image, manifest, k)
        # decode returns bytes as stored (already layout-transformed); compare to the same transform
        import layouts
        expected = np.frombuffer(layouts.transform("nhwc", inputs[k]), dtype=np.int8)
        assert tensor_bytes.tobytes() == expected.tobytes()
        assert label == int(labels[k])


def test_determinism_same_inputs_same_sha():
    inputs, labels = _synthetic(30, (16,))
    img1, m1 = pack_records.pack(inputs, labels, scale=0.5, zero_point=0)
    img2, m2 = pack_records.pack(inputs, labels, scale=0.5, zero_point=0)
    assert img1 == img2
    assert m1["sha256"] == m2["sha256"]


def test_limit_and_seed_deterministic_subset():
    inputs, labels = _synthetic(100, (16,))
    a, _ = pack_records.pack(inputs, labels, 1.0, 0, limit=10, seed=123)
    b, _ = pack_records.pack(inputs, labels, 1.0, 0, limit=10, seed=123)
    c, _ = pack_records.pack(inputs, labels, 1.0, 0, limit=10, seed=456)
    assert a == b            # same seed -> identical
    assert a != c            # different seed -> (almost surely) different subset
    # no-seed limit takes the first N deterministically
    d, md = pack_records.pack(inputs, labels, 1.0, 0, limit=10)
    assert md["record_count"] == 10


def test_capacity_guard_rejects_oversize():
    # 200 records of a ~150 KB stride blow past the store -> must raise with the max that fits
    inputs, labels = _synthetic(200, (150528,))
    with pytest.raises(ValueError, match="max that fits"):
        pack_records.pack(inputs, labels, 1.0, 0, layout="raw")


def test_reserve_weights_shrinks_capacity():
    stride = packlib.stride_for(150528)
    without = packlib.max_records(stride)
    with_w = packlib.max_records(stride, reserve_weights=11_690_000)
    assert with_w < without
    assert with_w == 33     # ResNet-18 row from docs/record_format.md


def test_quantize_float_inputs_bit_exact_vs_reference():
    rng = np.random.default_rng(1)
    inputs = rng.uniform(-2, 2, size=(5, 3, 4, 4)).astype(np.float32)
    labels = np.zeros(5, dtype=np.int64)
    scale, zp = 0.03, -4
    image, manifest = pack_records.pack(inputs, labels, scale, zp, layout="raw")
    assert manifest["quantized"] is True
    # independently quantize sample 0 and compare the stored bytes
    ref = packlib.quantize_int8(inputs[0], scale, zp)
    tensor_bytes, _ = inspect_recimg.decode(image, manifest, 0)
    assert tensor_bytes.tobytes() == ref.tobytes()


def test_verify_catches_corruption():
    inputs, labels = _synthetic(5, (16,))
    image, manifest = pack_records.pack(inputs, labels, 1.0, 0)
    # corrupt a pad byte
    stride, n = manifest["stride"], manifest["n_input_bytes"]
    bad = bytearray(image)
    bad[n + 2] = 0xFF               # a pad byte in record 0
    errs = inspect_recimg.verify(bytes(bad), manifest)
    assert any("pad" in e or "SHA-256" in e for e in errs)


def test_ragged_inputs_rejected():
    # inputs of differing per-sample size (object array) should fail cleanly
    inputs = np.array([np.zeros(4, np.int8), np.zeros(8, np.int8)], dtype=object)
    labels = np.array([0, 1])
    with pytest.raises((ValueError, TypeError)):
        pack_records.pack(inputs, labels, 1.0, 0)


def test_cli_end_to_end(tmp_path):
    import json
    inputs, labels = _synthetic(16, (3, 4, 4))
    npz = tmp_path / "data.npz"
    np.savez(npz, inputs=inputs, labels=labels)
    qm = tmp_path / "q.json"
    qm.write_text(json.dumps({"scale": 1.0, "zero_point": 0, "dataset": "syn", "layout": "raw"}))
    out = tmp_path / "syn.recimg"
    rc = pack_records.main(["--inputs", str(npz), "--quant-manifest", str(qm), "--out", str(out)])
    assert rc == 0
    assert out.exists() and (tmp_path / "syn.recimg.manifest.json").exists()
    rc2 = inspect_recimg.main([str(out), "--check"])
    assert rc2 == 0
