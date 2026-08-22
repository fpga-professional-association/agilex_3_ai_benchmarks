#!/usr/bin/env python3
"""Emit the deterministic synthetic bring-up image as a DLA input buffer.

Byte-for-byte the same stimulus the Nios V firmware used
(`initialize_synthetic_image` in
fpga/axc3000_mlperf/software/fpga_ai_resnet8/main.c):

    state = 0x13579bdf
    for i in 0..3071:                       # HWC order: i = pixel*3 + channel
        state = (state * 1664525 + 1013904223) mod 2**32
        synthetic_image[i] = (((state >> 24) ^ (i * 29)) & 0xff) ^ 0x80

The DLA input tensor element index is pixel-major with a channel vector:

    element = (h*32 + w) * CVEC + c,  c in 0..CVEC-1 (channels 3.. are zero)

and each element is an IEEE binary16 little-endian encoding of the exact
integer 0..255 -- identical to what tools/cifar10_to_dla.py writes.

Usage:  make_synthetic_input.py <out.bin> [--cvec 8]
"""
import argparse
import hashlib
import struct
import sys

import numpy as np

H = W = 32
C = 3


def synthetic_hwc():
    out = np.empty(H * W * C, dtype=np.uint8)
    state = 0x13579BDF
    for i in range(H * W * C):
        state = (state * 1664525 + 1013904223) & 0xFFFFFFFF
        out[i] = (((state >> 24) ^ (i * 29)) & 0xFF) ^ 0x80
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--cvec", type=int, default=8)
    a = ap.parse_args()

    hwc = synthetic_hwc()
    buf = np.zeros(H * W * a.cvec, dtype=np.float16)
    px = hwc.reshape(H * W, C).astype(np.float16)
    buf.reshape(H * W, a.cvec)[:, :C] = px
    raw = buf.tobytes()
    assert len(raw) == H * W * a.cvec * 2
    with open(a.out, "wb") as f:
        f.write(raw)
    print(f"wrote {a.out}  {len(raw)} B  cvec={a.cvec}")
    print(f"sha256 {hashlib.sha256(raw).hexdigest()}")
    # HWC bytes, for an independent CPU-side re-derivation
    print(f"hwc sha256 {hashlib.sha256(hwc.tobytes()).hexdigest()}")
    print("hwc[0:12] =", list(hwc[:12]))
    # CHW view (what the logical `image_u8_nchw` tensor holds)
    chw = hwc.reshape(H * W, C).T.reshape(-1)
    np.save(a.out.replace(".bin", "_chw_uint8.npy"), chw)
    print("chw uint8 saved to", a.out.replace(".bin", "_chw_uint8.npy"))


if __name__ == "__main__":
    sys.exit(main())
