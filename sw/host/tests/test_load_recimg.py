"""Loader tests against MockTransport: chunked load, sampled read-back verify, guards (issue #17)."""

import hashlib
import json
from pathlib import Path

import pytest

import load_recimg
from transport import MockTransport


def _write_image(tmp_path: Path, size: int) -> Path:
    img = bytes((i * 7 + 3) & 0xFF for i in range(size))
    p = tmp_path / "t.recimg"
    p.write_bytes(img)
    manifest = {"sha256": hashlib.sha256(img).hexdigest(), "record_count": 4, "stride": size // 4,
                "hr_bytes": 16 * 1024 * 1024, "log_reserve": 65536}
    Path(str(p) + ".manifest.json").write_text(json.dumps(manifest))
    return p


def test_load_and_verify_roundtrips(tmp_path):
    p = _write_image(tmp_path, 200_000)          # spans several 64 KB chunks
    t = MockTransport(mem_size=16 * 1024 * 1024)
    seen = []
    manifest = load_recimg.load_and_verify(t, p, rec_base=0x1000,
                                           progress=lambda d, n: seen.append((d, n)))
    assert manifest["record_count"] == 4
    assert t.read_block(0x1000, 200_000) == p.read_bytes()
    assert seen and seen[-1][0] == 200_000       # progress reached 100%


def test_verify_catches_corrupted_readback(tmp_path):
    p = _write_image(tmp_path, 8192)
    t = MockTransport(mem_size=1 << 20)
    image = p.read_bytes()
    load_recimg.load_image(t, image, rec_base=0)
    t.mem[100] ^= 0xFF                            # corrupt one byte in HyperRAM
    with pytest.raises(ValueError, match="read-back mismatch"):
        load_recimg.verify_load(t, image, rec_base=0)


def test_manifest_sha_mismatch_rejected(tmp_path):
    p = _write_image(tmp_path, 4096)
    # tamper the manifest hash
    mpath = Path(str(p) + ".manifest.json")
    man = json.loads(mpath.read_text())
    man["sha256"] = "0" * 64
    mpath.write_text(json.dumps(man))
    t = MockTransport(mem_size=1 << 20)
    with pytest.raises(ValueError, match="manifest SHA-256"):
        load_recimg.load_and_verify(t, p, rec_base=0)


def test_load_refuses_to_overrun_log_reserve(tmp_path):
    p = _write_image(tmp_path, 4096)
    t = MockTransport(mem_size=16 * 1024 * 1024)
    image = p.read_bytes()
    hr = 16 * 1024 * 1024
    log_top = hr - 65536
    # place the image so it would cross into the reserve
    with pytest.raises(ValueError, match="log reserve"):
        load_recimg.load_image(t, image, rec_base=log_top - 100, log_reserve_top=log_top)
