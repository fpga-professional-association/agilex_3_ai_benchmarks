#!/usr/bin/env python3
"""Independent standard-library ResNet8 integer regression path.

This intentionally does not import generated_model.h or execute the C
implementation.  It parses the exact FlatBuffer with tflite_introspect.py,
decodes its buffers/options, and evaluates the graph in Python.  Its output is
an implementation regression vector, not a TFLite-runtime or MLPerf oracle.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import tflite_introspect as ti  # noqa: E402

I32_MIN, I32_MAX = -(1 << 31), (1 << 31) - 1

def sat32(x): return max(I32_MIN, min(I32_MAX, int(x)))
def sat8(x): return max(-128, min(127, int(x)))

def high_mul(a, b):
    if a == I32_MIN and b == I32_MIN: return I32_MAX
    p = int(a) * int(b)
    nudge = (1 << 30) if p >= 0 else 1 - (1 << 30)
    # C++ integer division truncates toward zero; avoid Python float division
    # because products near 2^62 cannot be represented exactly as a float.
    num = p + nudge
    q = abs(num) // (1 << 31)
    return sat32(-q if num < 0 else q)

def round_pot(x, exponent):
    if exponent <= 0: return x
    mask = (1 << exponent) - 1
    # Python's // and % give the same result as an arithmetic C right shift
    # plus a nonnegative bit remainder for two's-complement values.
    return (x >> exponent) + (((x & mask) > ((mask >> 1) + (x < 0))))

def mul_q(x, m, shift):
    if shift >= 0: return high_mul(sat32(int(x) * (1 << shift)), m)
    return round_pot(high_mul(x, m), -shift)

def qmult(real):
    if real == 0: return 0, 0
    sig, exp = math.frexp(real)
    # No model multiplier is a tie; this matches the generated constants.
    q = int(round(sig * (1 << 31)))
    if q == (1 << 31): q //= 2; exp += 1
    return q, exp

def fnv(data):
    h = 2166136261
    for x in data:
        h = ((h ^ (x & 255)) * 16777619) & 0xffffffff
    return h

def synthetic():
    x, out = 0x13579BDF, []
    for i in range(32 * 32 * 3):
        x = (x * 1664525 + 1013904223) & 0xffffffff
        v = ((x >> 24) ^ (i * 29)) & 255
        out.append(v - 256 if v >= 128 else v)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    manifest = json.loads((ROOT / "model/model_manifest.json").read_text())
    model_path = ROOT / "third_party/mlcommons_tiny/benchmark/training/image_classification/trained_models/pretrainedResnet_quant.tflite"
    model = model_path.read_bytes()
    assert hashlib.sha256(model).hexdigest() == manifest["model"]["sha256"]
    parsed = ti.parse_model(model, str(model_path)); sg = parsed["subgraphs"][0]
    ts = {t["index"]: t for t in sg["tensors"]}; bs = parsed["buffers"]
    def raw(ti_):
        b = bs[ts[ti_]["buffer"]]
        return model[b["data_offset"]:b["data_offset"] + b["size"]] if b["size"] else b""
    def i8(ti_): return list(struct.unpack("<%db" % len(raw(ti_)), raw(ti_)))
    def i32(ti_): return list(struct.unpack("<%di" % (len(raw(ti_)) // 4), raw(ti_)))
    def q(ti_): return ts[ti_]["quantization"]["scale"][0], ts[ti_]["quantization"]["zero_point"][0]
    def conv(src, op):
        inp, wt, bi, outi = op["inputs"][0], op["inputs"][1], op["inputs"][2], op["outputs"][0]
        ih, iw, ic = ts[inp]["shape"][1:]; oh, ow, oc = ts[outi]["shape"][1:]
        kh, kw = ts[wt]["shape"][1:3]; sh, sw = op["builtin_options"]["stride_h"], op["builtin_options"]["stride_w"]
        izp, ozp = q(inp)[1], q(outi)[1]; os = q(outi)[0]
        ws = ts[wt]["quantization"]["scale"]; w, bias = i8(wt), i32(bi)
        ms = [qmult(q(inp)[0] * ws[k] / os) for k in range(oc)]
        ph = max(0, oh * sh - ih + kh - sh) // 2; pw = max(0, ow * sw - iw + kw - sw) // 2
        dst = [0] * (oh * ow * oc); relu = op["builtin_options"]["fused_activation"]["name"] == "RELU"
        for y in range(oh):
          for x in range(ow):
            for k in range(oc):
              acc = bias[k]
              for ky in range(kh):
                iy = y * sh + ky - ph
                for kx in range(kw):
                  ix = x * sw + kx - pw
                  for ci in range(ic):
                    a = src[(iy * iw + ix) * ic + ci] - izp if 0 <= iy < ih and 0 <= ix < iw else 0
                    acc += a * w[((k * kh + ky) * kw + kx) * ic + ci]
              z = mul_q(acc, ms[k][0], ms[k][1]) + ozp
              if relu: z = max(z, ozp)
              dst[(y * ow + x) * oc + k] = sat8(z)
        return dst
    def add(a, b, op):
        a0, b0, oi = op["inputs"][0], op["inputs"][1], op["outputs"][0]
        m1, s1 = qmult(q(a0)[0] / q(oi)[0]); m2, s2 = qmult(q(b0)[0] / q(oi)[0]); ozp = q(oi)[1]
        relu = op["builtin_options"]["fused_activation"]["name"] == "RELU"
        out = []
        for x, y in zip(a, b):
            z = mul_q(x - q(a0)[1], m1, s1) + mul_q(y - q(b0)[1], m2, s2) + ozp
            out.append(sat8(max(z, ozp) if relu else z))
        return out
    ops = sg["operators"]; x = synthetic(); tensors = {}; checks = []
    for op in ops:
        name = op["operator"]
        if name == "CONV_2D": y = conv(tensors.get(op["inputs"][0], x), op)
        elif name == "ADD": y = add(tensors[op["inputs"][0]], tensors[op["inputs"][1]], op)
        elif name == "AVERAGE_POOL_2D":
            src = tensors[op["inputs"][0]]; y = []
            for c in range(64):
                s = sum(src[(yy * 8 + xx) * 64 + c] + 128 for yy in range(8) for xx in range(8))
                y.append(sat8(mul_q(s, 1073741824, -6) - 128))
        elif name == "RESHAPE": y = list(tensors[op["inputs"][0]])
        elif name == "FULLY_CONNECTED":
            src = tensors[op["inputs"][0]]; wt, bi = op["inputs"][1:]; oi = op["outputs"][0]; m, sh = qmult(q(op["inputs"][0])[0] * ts[wt]["quantization"]["scale"][0] / q(oi)[0]); w, b = i8(wt), i32(bi); y = []
            for r in range(10): y.append(sat8(mul_q(b[r] + sum((src[c] - q(op["inputs"][0])[1]) * w[r * 64 + c] for c in range(64)), m, sh) + q(oi)[1]))
        elif name == "SOFTMAX":
            src = tensors[op["inputs"][0]]; scale = q(op["inputs"][0])[0]; mx = max(src); es = [round(math.exp(-(mx-v)*scale) * 32768) if mx-v < 256 else 0 for v in src]; sm = sum(es) or 1; y = [sat8((e * 256 + sm // 2) // sm - 128) for e in es]
        else: raise AssertionError(name)
        tensors[op["outputs"][0]] = y
        checks.append(fnv(y))
    result = {"layers": checks, "class": max(range(10), key=lambda i: y[i]), "checksum": fnv(y)}
    print(json.dumps(result) if args.json else result)

if __name__ == "__main__": main()
