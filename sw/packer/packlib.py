"""Pure record-packing logic for the HyperRAM record store (issue #5).

No I/O, no OpenVINO, no CLI — just the math from docs/record_format.md, so it is trivially unit
tested. pack_records.py and inspect_recimg.py build on this.

Record layout (docs/record_format.md):
    [ input tensor, INT8, engine-native layout : N bytes ][ golden label : 1 byte ][ zero pad ]
    stride = ceil((N + 1) / 64) * 64        # 64-byte burst alignment
Records sit back-to-back, `stride` bytes apart, starting at REC_BASE. The top LOG_RESERVE bytes of
HyperRAM hold the result log and must never be written by the record store.
"""

from __future__ import annotations

import numpy as np

# Board/plan constants (PLAN §1, §6).
HR_BYTES = 16 * 1024 * 1024      # Winbond W957D8NB, 16 MB
LOG_RESERVE = 64 * 1024          # result-log reserve at the top of HyperRAM
ONCHIP_W_LIMIT = 400 * 1024      # design line: weights above this live in HyperRAM (PLAN §5)
BURST_ALIGN = 64                 # record stride granularity


def stride_for(n_input_bytes: int) -> int:
    """Record stride in bytes for an N-byte input tensor: ceil((N+1)/64)*64."""
    if n_input_bytes < 0:
        raise ValueError("n_input_bytes must be non-negative")
    total = n_input_bytes + 1  # + golden label byte
    return ((total + BURST_ALIGN - 1) // BURST_ALIGN) * BURST_ALIGN


def available_bytes(hr_bytes: int = HR_BYTES, log_reserve: int = LOG_RESERVE,
                    reserve_weights: int = 0) -> int:
    """Bytes usable by the record store: HyperRAM minus the log reserve and any resident weights."""
    avail = hr_bytes - log_reserve - reserve_weights
    if avail < 0:
        raise ValueError("reserve exceeds HyperRAM capacity")
    return avail


def max_records(stride: int, hr_bytes: int = HR_BYTES, log_reserve: int = LOG_RESERVE,
                reserve_weights: int = 0) -> int:
    """How many stride-sized records fit below the log reserve (and resident weights)."""
    if stride <= 0:
        raise ValueError("stride must be positive")
    return available_bytes(hr_bytes, log_reserve, reserve_weights) // stride


def quantize_int8(x: np.ndarray, scale: float, zero_point: int) -> np.ndarray:
    """Quantize float inputs to INT8 exactly as the compiled IR does (docs/record_format.md).

    q = clip(round_half_to_even(x / scale) + zero_point, -128, 127).  numpy's rint is
    round-half-to-even, matching OpenVINO. If x is already int8, it is returned unchanged.
    """
    x = np.asarray(x)
    if x.dtype == np.int8:
        return x
    if scale == 0:
        raise ValueError("scale must be non-zero")
    q = np.rint(x.astype(np.float64) / scale) + zero_point
    q = np.clip(q, -128, 127)
    return q.astype(np.int8)


def build_record(input_bytes: bytes, label: int, stride: int) -> bytes:
    """Assemble one record: input tensor bytes, 1 label byte, zero pad to `stride`."""
    n = len(input_bytes)
    if not (0 <= label <= 255):
        raise ValueError(f"label {label} out of byte range")
    if stride != stride_for(n):
        raise ValueError(f"stride {stride} != stride_for({n})={stride_for(n)}")
    rec = bytearray(stride)                 # zero-initialized -> pad is already zero
    rec[:n] = input_bytes
    rec[n] = label & 0xFF
    return bytes(rec)


def label_at(record: bytes, n_input_bytes: int) -> int:
    """Golden label byte of a record given the input length."""
    return record[n_input_bytes]
