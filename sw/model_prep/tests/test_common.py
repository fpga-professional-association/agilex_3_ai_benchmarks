"""Pure-logic tests for common.py -- no network, no heavy ML deps (issue #2)."""

from __future__ import annotations

import hashlib
import json

import pytest

import common


def test_sha256_file(tmp_path):
    p = tmp_path / "f.bin"
    p.write_bytes(b"hello world")
    assert common.sha256_file(p) == hashlib.sha256(b"hello world").hexdigest()


def test_tool_versions_includes_python_and_omits_missing():
    versions = common.tool_versions("numpy", "not_a_real_package_xyz")
    assert versions["python"]
    assert "numpy" in versions
    assert "not_a_real_package_xyz" not in versions


def test_model_manifest_roundtrip(tmp_path):
    manifest = common.ModelManifest(
        model_id="foo", source_url="http://example/x", source_commit="abc123",
        sha256="deadbeef", param_count=42, input_shape=[1, 2, 3], layout="NHWC",
        preprocessing="none", opset=13, tool_versions={"python": "3.12"}, notes="n/a",
    )
    path = tmp_path / "foo.manifest.json"
    manifest.write(path)
    data = json.loads(path.read_text())
    assert data["model_id"] == "foo"
    assert data["param_count"] == 42
    assert data["input_shape"] == [1, 2, 3]


def test_write_result_shape(tmp_path):
    path = tmp_path / "ph2_foo-fp32_20260704.json"
    common.write_result(
        path, kind="reference", level="PH2", subject="foo-fp32", date="2026-07-04",
        config={"device": "A3CY100BM16AE7S", "tool_versions": {"python": "3.12"}},
        metrics={"accuracy_top1": 0.9},
        notes="test",
    )
    data = json.loads(path.read_text())
    assert data["kind"] == "reference"
    assert data["level"] == "PH2"
    assert data["metrics"]["accuracy_top1"] == 0.9
    assert data["config"]["device"] == "A3CY100BM16AE7S"


def test_write_result_validates_against_schema(tmp_path):
    """Cross-check against the real result.schema.json (scripts/reslib.py), same rules CI runs."""
    pytest.importorskip("jsonschema")
    import sys
    from pathlib import Path

    sys.path.insert(0, str(common.REPO_ROOT / "scripts"))
    import reslib

    path = tmp_path / "ph2_foo-fp32_20260704.json"
    common.write_result(
        path, kind="reference", level="PH2", subject="foo-fp32", date="2026-07-04",
        config={"device": "A3CY100BM16AE7S", "tool_versions": {"python": "3.12"}},
        metrics={"accuracy_top1": 0.9},
    )
    data = json.loads(path.read_text())
    validator = reslib.make_validator()
    errors = reslib.validate_result(data, validator)
    assert errors == []


def test_download_is_idempotent_and_skips_matching_hash(tmp_path, monkeypatch):
    pytest.importorskip("requests")
    import requests

    calls = {"n": 0}

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"payload-bytes"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_get(url, stream=True, timeout=120):
        calls["n"] += 1
        return FakeResponse()

    monkeypatch.setattr(requests, "get", fake_get)

    dest = tmp_path / "sub" / "file.bin"
    expected_sha = hashlib.sha256(b"payload-bytes").hexdigest()

    common.download("http://example/file.bin", dest, sha256=expected_sha)
    assert calls["n"] == 1
    assert dest.read_bytes() == b"payload-bytes"

    # second call: file already present with matching hash -> no network call
    common.download("http://example/file.bin", dest, sha256=expected_sha)
    assert calls["n"] == 1


def test_download_rejects_hash_mismatch(tmp_path, monkeypatch):
    pytest.importorskip("requests")
    import requests

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size):
            yield b"wrong-bytes"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(requests, "get", lambda *a, **k: FakeResponse())

    dest = tmp_path / "file.bin"
    with pytest.raises(ValueError):
        common.download("http://example/file.bin", dest, sha256="0" * 64)
    assert not dest.exists()
