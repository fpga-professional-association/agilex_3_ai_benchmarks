"""Load a packed record image into HyperRAM over a Transport (issue #17).

Writes `<name>.recimg` to REC_BASE in chunks (JTAG is ~1–2 MB/s, PLAN §8 E — a full 16 MB image is
minutes, so progress is printed), then verifies by reading back sampled windows and comparing
SHA-256 against the manifest's per-window hashes. The QSPI-staging path (PLAN §8 method C) is stubbed
for later.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CHUNK = 64 * 1024


def load_manifest(image_path: Path) -> dict:
    return json.loads(Path(str(image_path) + ".manifest.json").read_text())


def load_image(transport, image: bytes, rec_base: int, *, log_reserve_top: int | None = None,
               progress=None) -> None:
    """Write `image` to HyperRAM at `rec_base`, chunked. Refuses to write into the log reserve."""
    if log_reserve_top is not None and rec_base + len(image) > log_reserve_top:
        raise ValueError(f"image [{rec_base}, {rec_base+len(image)}) overruns the log reserve "
                         f"boundary at {log_reserve_top}")
    written = 0
    for off in range(0, len(image), CHUNK):
        block = image[off:off + CHUNK]
        transport.write_block(rec_base + off, block)
        written += len(block)
        if progress:
            progress(written, len(image))


def verify_load(transport, image: bytes, rec_base: int, n_windows: int = 8,
                window: int = 4096) -> None:
    """Read back `n_windows` evenly-spaced windows and compare SHA-256 to the source image.

    Raises on any mismatch. Sampling (not a full 16 MB read-back) keeps verification fast over JTAG
    while still catching addressing/wraparound bugs across the whole image.
    """
    n = len(image)
    if n == 0:
        return
    positions = sorted(set(
        [0, max(0, n - window)] +
        [min(n - 1, (i * n) // max(1, n_windows)) for i in range(n_windows)]))
    for pos in positions:
        w = min(window, n - pos)
        got = transport.read_block(rec_base + pos, w)
        exp = image[pos:pos + w]
        if hashlib.sha256(got).hexdigest() != hashlib.sha256(exp).hexdigest():
            raise ValueError(f"read-back mismatch at image offset {pos} ({w} B)")


def load_and_verify(transport, image_path: Path, rec_base: int, *, verify: bool = True,
                    progress=None) -> dict:
    """Load `<image_path>` + verify; returns its manifest (record_count/stride etc. for the runner)."""
    image = Path(image_path).read_bytes()
    manifest = load_manifest(Path(image_path))
    if hashlib.sha256(image).hexdigest() != manifest["sha256"]:
        raise ValueError("image does not match its manifest SHA-256 (corrupt image?)")
    log_top = manifest.get("hr_bytes", 0) - manifest.get("log_reserve", 0) if manifest.get("hr_bytes") else None
    load_image(transport, image, rec_base, log_reserve_top=log_top, progress=progress)
    if verify:
        verify_load(transport, image, rec_base)
    return manifest


def stage_from_qspi(*_args, **_kwargs):  # pragma: no cover
    """TODO (PLAN §8 method C): stage a >16 MB dataset from QSPI into HyperRAM between passes."""
    raise NotImplementedError("QSPI staging lands with the full-set accuracy passes")
