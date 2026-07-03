"""Pluggable INT8 tensor-layout serializers (issue #5).

Each layout turns one INT8 sample tensor into the exact byte string the engine consumes. Layout is
fixed at pack time — the DMA does NO reformatting (docs/record_format.md, PLAN §6). The engine-native
blocked layout from the dla_compiler report gets added by #18 once known; the interface stays
`transform(tensor) -> bytes`.

Image conventions (batch dimension already stripped):
  - raw:  serialize the array as given, C-order. Works for any shape (KWS/AD 1-D, etc.).
  - nchw: source is (C, H, W); serialize C-order (identity for an NCHW source, e.g. ONNX default).
  - nhwc: source is (C, H, W); transpose to (H, W, C) then serialize (channel-minor).
"""

from __future__ import annotations

import numpy as np

Transform = "Callable[[np.ndarray], bytes]"


def _as_int8(t: np.ndarray) -> np.ndarray:
    if t.dtype != np.int8:
        raise ValueError(f"layout expects an int8 tensor, got {t.dtype}")
    return t


def raw(t: np.ndarray) -> bytes:
    return np.ascontiguousarray(_as_int8(t)).tobytes(order="C")


def nchw(t: np.ndarray) -> bytes:
    t = _as_int8(t)
    if t.ndim != 3:
        raise ValueError(f"nchw layout expects a 3-D (C,H,W) tensor, got shape {t.shape}")
    return np.ascontiguousarray(t).tobytes(order="C")


def nhwc(t: np.ndarray) -> bytes:
    t = _as_int8(t)
    if t.ndim != 3:
        raise ValueError(f"nhwc layout expects a 3-D (C,H,W) tensor, got shape {t.shape}")
    return np.ascontiguousarray(np.transpose(t, (1, 2, 0))).tobytes(order="C")


LAYOUTS = {"raw": raw, "nchw": nchw, "nhwc": nhwc}


def get(name: str):
    if name not in LAYOUTS:
        raise KeyError(f"unknown layout '{name}'; choices: {sorted(LAYOUTS)}")
    return LAYOUTS[name]


def transform(name: str, tensor: np.ndarray) -> bytes:
    return get(name)(tensor)
