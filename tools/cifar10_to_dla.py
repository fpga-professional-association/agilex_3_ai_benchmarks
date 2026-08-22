#!/usr/bin/env python3
"""CIFAR-10 -> CoreDLA / FPGA AI Suite input-buffer converter.

Produces one contiguous input buffer per image, laid out exactly the way the
FPGA AI Suite "input transform" expects it in DDR, plus the matching label
array and per-image SHA-256 digests.

------------------------------------------------------------------------------
Layout (authoritative source: the compiler's own mapping CSV)
------------------------------------------------------------------------------
build/fpga_ai/compile_vendor_8x8/TensorFlow_Lite_Frontend_IR/
    input_transform_mapping_TensorFlow_Lite_Frontend_IR_0.csv

    logical input tensor offset -> AI suite input tensor offset

The logical tensor is named `image_u8_nchw` and is indexed CHW:

    logical_index = c*H*W + h*W + w                     (c in 0..2)

The AI Suite element index is pixel-major with a channel vector of `c_vector`
(the arch file's `c_vector`, 8 for resnet8_agx3_vendor_8x8.arch):

    element_index = (h*W + w)*CVEC + c                  (c in 0..CVEC-1)

Channels 3..CVEC-1 are padding and are zero.  With FP16 elements that is

    32 * 32 * 8 elements * 2 B = 16384 B per image

which matches `ddr_buffer_info_*.txt`: "image_u8_nchw: offset 0, size: 16384".

NOTE: 8192 is the *element* count for CVEC=8, not the byte count.  A CVEC=4
FP16 buffer would be 8192 B; the currently-programmed design is CVEC=8.
Use --cvec to change it.

CIFAR-10's python batch files store each image as 3072 uint8 already in CHW
(1024 R, then 1024 G, then 1024 B) -- i.e. the raw row IS the logical tensor,
so the "transpose" is exactly the (h*W+w)*CVEC + c scatter and nothing else.
(MLPerf Tiny's own perf_samples_loader.py instead rolls the axis to HWC before
flattening, because the EEMBC .bin stimulus format is interleaved U8C3; that is
a different consumer, not our DDR layout.)

------------------------------------------------------------------------------
Element formats (pluggable -- see ELEMENT_FORMATS)
------------------------------------------------------------------------------
fp16   2 B/element, IEEE binary16 little-endian.  0..255 is exactly
       representable in binary16 (integers are exact up to 2048).
       This is what the currently-programmed FP12AGX design consumes.
int8   1 B/element, value = clamp(u8 + zero_point_offset).  Default offset
       -128 reproduces the TFLite int8 semantics used by MLPerf Tiny's
       tflite_test.py (`test_imgs.astype(np.int64) - 128`).
uint8  1 B/element, raw 0..255 passthrough.

The int8 variant's *layout* (CVEC, and whether padding is 0x00 or 0x80) is NOT
yet confirmed for an int8 arch -- it is parameterised here (--cvec, --pad-byte)
so the arch investigation can pin it down without touching this code.

------------------------------------------------------------------------------
Usage
------------------------------------------------------------------------------
    # extract test_batch out of the downloaded tarball, verify checksums
    python tools/cifar10_to_dla.py extract \
        --tar   build/fpga_ai/cifar10/cifar-10-python.tar.gz \
        --dest  build/fpga_ai/cifar10

    # convert the official MLPerf Tiny 200-image perf subset
    python tools/cifar10_to_dla.py convert \
        --cifar-dir build/fpga_ai/cifar10/cifar-10-batches-py \
        --subset perf \
        --perf-idxs third_party/mlcommons_tiny/benchmark/training/image_classification/perf_samples_idxs.npy \
        --format fp16 --cvec 8 \
        --out-dir build/fpga_ai/cifar10/dla_perf200_fp16 \
        --ppm 3 --selftest \
        --verify-mapping build/fpga_ai/compile_vendor_8x8/TensorFlow_Lite_Frontend_IR/input_transform_mapping_TensorFlow_Lite_Frontend_IR_0.csv

    # convert the full 10k test set, or a 5000-image half (10k-inference cap)
    python tools/cifar10_to_dla.py convert --subset all  ...
    python tools/cifar10_to_dla.py convert --subset range --start 0 --count 5000 ...
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import pickle
import sys
import tarfile
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# Published checksums
# ---------------------------------------------------------------------------
# Archive md5 as published by torchvision.datasets.CIFAR10 (tgz_md5) for
# https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz
ARCHIVE_MD5 = "c58f30108f718f92721af3b95e74349a"

# Per-member md5s, likewise from torchvision.datasets.CIFAR10 train_list /
# test_list / meta.
MEMBER_MD5 = {
    "data_batch_1": "c99cafc152244af753f735de768cd75f",
    "data_batch_2": "d4bba439e000b95fd0a9bffe97cbabec",
    "data_batch_3": "54ebc095f3ab1f0389bbae665268c751",
    "data_batch_4": "634d18415352ddfa80567beed471001a",
    "data_batch_5": "482c414d41f54cd18b22e5b47cb7c3cb",
    "test_batch": "40351d587109b95175f43aff81a1287e",
    "batches.meta": "5ff9c542aee3614f3951f8cda6e48888",
}

IMG_H = 32
IMG_W = 32
IMG_C = 3
CIFAR_ROW_BYTES = IMG_C * IMG_H * IMG_W  # 3072, CHW

CLASS_NAMES = [
    "airplane", "automobile", "bird", "cat", "deer",
    "dog", "frog", "horse", "ship", "truck",
]


# ---------------------------------------------------------------------------
# Pluggable element writers
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ElementFormat:
    """Encodes a uint8 pixel plane into fixed-width little-endian elements.

    `encode` maps a uint8 array of shape (..., 3) to a uint8 array of shape
    (..., 3, bytes_per_element) holding the raw little-endian bytes.
    """
    name: str
    bytes_per_element: int
    encode: Callable[[np.ndarray, "ConvertOptions"], np.ndarray]
    description: str


def _encode_fp16(plane_u8: np.ndarray, opts: "ConvertOptions") -> np.ndarray:
    # binary16 is exact for every integer in 0..255 (11-bit significand).
    half = plane_u8.astype("<f2")
    if not np.array_equal(half.astype(np.float64), plane_u8.astype(np.float64)):
        raise AssertionError("fp16 round-trip is not exact -- impossible for 0..255")
    return np.ascontiguousarray(half).view(np.uint8).reshape(*plane_u8.shape, 2)


def _encode_int8(plane_u8: np.ndarray, opts: "ConvertOptions") -> np.ndarray:
    shifted = plane_u8.astype(np.int16) + opts.int8_zero_point_offset
    if shifted.min() < -128 or shifted.max() > 127:
        raise ValueError(
            f"int8 offset {opts.int8_zero_point_offset} pushes values out of range "
            f"[{shifted.min()}, {shifted.max()}]")
    raw = shifted.astype(np.int8).view(np.uint8).reshape(*plane_u8.shape, 1)
    return raw


def _encode_uint8(plane_u8: np.ndarray, opts: "ConvertOptions") -> np.ndarray:
    return plane_u8.reshape(*plane_u8.shape, 1).copy()


ELEMENT_FORMATS: Dict[str, ElementFormat] = {
    "fp16": ElementFormat(
        "fp16", 2, _encode_fp16,
        "IEEE binary16 LE, exact 0..255 (current FP12AGX design)"),
    "int8": ElementFormat(
        "int8", 1, _encode_int8,
        "signed int8, value = u8 + zero_point_offset (default -128, TFLite)"),
    "uint8": ElementFormat(
        "uint8", 1, _encode_uint8,
        "raw uint8 passthrough"),
}


# ---------------------------------------------------------------------------
# Options
# ---------------------------------------------------------------------------
@dataclass
class ConvertOptions:
    fmt: ElementFormat
    cvec: int
    pad_byte: int
    int8_zero_point_offset: int

    @property
    def bytes_per_image(self) -> int:
        return IMG_H * IMG_W * self.cvec * self.fmt.bytes_per_element


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _md5(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def _sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()


def _md5_bytes(b: bytes) -> str:
    return hashlib.md5(b).hexdigest()


def load_test_batch(cifar_dir: str):
    """Return (data_chw_u8 (N,3072), labels (N,), filenames (N,))."""
    path = os.path.join(cifar_dir, "test_batch")
    with open(path, "rb") as f:
        raw = f.read()
    got = _md5_bytes(raw)
    want = MEMBER_MD5["test_batch"]
    if got != want:
        raise SystemExit(f"test_batch md5 mismatch: got {got}, expected {want}")
    d = pickle.loads(raw, encoding="bytes")
    data = np.asarray(d[b"data"], dtype=np.uint8)
    labels = np.asarray(d[b"labels"], dtype=np.int64)
    filenames = np.asarray([n.decode("utf-8") for n in d[b"filenames"]])
    if data.shape != (10000, CIFAR_ROW_BYTES):
        raise SystemExit(f"unexpected test_batch shape {data.shape}")
    return data, labels, filenames


# ---------------------------------------------------------------------------
# Core conversion
# ---------------------------------------------------------------------------
def convert_images(rows_chw: np.ndarray, opts: ConvertOptions) -> np.ndarray:
    """rows_chw: (N, 3072) uint8, CHW as stored in the CIFAR-10 batch file.

    Returns (N, bytes_per_image) uint8, DLA DDR layout:
        byte = ((h*W + w)*CVEC + c)*bpe + k
    """
    if opts.cvec < IMG_C:
        raise ValueError(f"--cvec {opts.cvec} < {IMG_C} data channels")
    n = rows_chw.shape[0]
    bpe = opts.fmt.bytes_per_element

    # (N, C, H, W) -> (N, H, W, C).  This is the only transpose in the pipeline.
    chw = rows_chw.reshape(n, IMG_C, IMG_H, IMG_W)
    hwc = np.ascontiguousarray(chw.transpose(0, 2, 3, 1))  # (N, H, W, 3)

    enc = np.full((n, IMG_H, IMG_W, opts.cvec, bpe),
                  opts.pad_byte, dtype=np.uint8)
    enc[:, :, :, :IMG_C, :] = opts.fmt.encode(hwc, opts)

    buf = enc.reshape(n, opts.bytes_per_image)
    assert buf.shape[1] == opts.bytes_per_image
    return buf


def decode_images(buf: np.ndarray, opts: ConvertOptions) -> np.ndarray:
    """Inverse of convert_images: (N, bytes) -> (N, H, W, 3) uint8."""
    n = buf.shape[0]
    bpe = opts.fmt.bytes_per_element
    enc = buf.reshape(n, IMG_H, IMG_W, opts.cvec, bpe)
    data = np.ascontiguousarray(enc[:, :, :, :IMG_C, :])
    if opts.fmt.name == "fp16":
        vals = data.reshape(n, IMG_H, IMG_W, IMG_C * bpe).view("<f2")
        vals = vals.reshape(n, IMG_H, IMG_W, IMG_C)
        return vals.astype(np.uint8)
    if opts.fmt.name == "int8":
        vals = data.view(np.int8).reshape(n, IMG_H, IMG_W, IMG_C).astype(np.int16)
        return (vals - opts.int8_zero_point_offset).astype(np.uint8)
    return data.reshape(n, IMG_H, IMG_W, IMG_C)


def write_ppm(path: str, img_hwc_u8: np.ndarray) -> None:
    """Binary P6 PPM -- no matplotlib/PIL dependency."""
    h, w, c = img_hwc_u8.shape
    assert c == 3
    with open(path, "wb") as f:
        f.write(b"P6\n%d %d\n255\n" % (w, h))
        f.write(np.ascontiguousarray(img_hwc_u8, dtype=np.uint8).tobytes())


def write_png(path: str, img_hwc_u8: np.ndarray, scale: int = 8) -> Optional[str]:
    """PNG via Pillow if it happens to be installed; None if it is not.

    Nearest-neighbour upscaled so a 32x32 thumbnail is actually eyeballable.
    """
    try:
        from PIL import Image  # type: ignore
    except Exception:
        return None
    h, w, _ = img_hwc_u8.shape
    im = Image.fromarray(np.ascontiguousarray(img_hwc_u8, dtype=np.uint8), "RGB")
    if scale > 1:
        im = im.resize((w * scale, h * scale), Image.NEAREST)
    im.save(path)
    return path


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def verify_mapping_csv(csv_path: str, opts: ConvertOptions) -> str:
    """Cross-check our index formula against the AI Suite's own mapping CSV.

    The CSV maps `logical input tensor offset` (CHW) -> `AI suite input tensor
    offset` (element index).  We assert both against our closed forms.
    """
    checked = 0
    with open(csv_path, "r", newline="") as f:
        rdr = csv.reader(f, delimiter=";")
        header = next(rdr)
        col = {name.strip(): i for i, name in enumerate(header)}
        li = col["logical input tensor offset"]
        ai = col["AI suite input tensor offset"]
        ci = col["input channel (C)"]
        hi = col["input height (H)"]
        wi = col["input width (W)"]
        cvi = col["transformed C-vector (Cvec)"]
        for row in rdr:
            if not row or not row[0].strip():
                continue
            logical = int(row[li]); aio = int(row[ai])
            c = int(row[ci]); h = int(row[hi]); w = int(row[wi])
            cv = int(row[cvi])
            if logical != c * IMG_H * IMG_W + h * IMG_W + w:
                raise AssertionError(
                    f"logical offset {logical} is not CHW for (c={c},h={h},w={w})")
            if cv != c:
                raise AssertionError(
                    f"Cvec slot {cv} != channel {c} at logical {logical}")
            if aio != (h * IMG_W + w) * opts.cvec + c:
                raise AssertionError(
                    f"element index {aio} != (h*W+w)*{opts.cvec}+c for "
                    f"(c={c},h={h},w={w}) -- wrong --cvec?")
            checked += 1
    if checked != CIFAR_ROW_BYTES:
        raise AssertionError(f"mapping CSV had {checked} rows, expected {CIFAR_ROW_BYTES}")
    return (f"mapping CSV OK: {checked} entries; logical=CHW, "
            f"element=(h*32+w)*{opts.cvec}+c confirmed")


def selftest(rows_chw: np.ndarray, buf: np.ndarray, opts: ConvertOptions) -> List[str]:
    out: List[str] = []
    n = rows_chw.shape[0]

    if buf.shape != (n, opts.bytes_per_image):
        raise AssertionError(f"buffer shape {buf.shape} != {(n, opts.bytes_per_image)}")
    out.append(f"shape OK: {n} x {opts.bytes_per_image} B "
               f"({IMG_H}*{IMG_W}*{opts.cvec} elements * {opts.fmt.bytes_per_element} B)")

    # Round-trip through the decoder.
    hwc_src = rows_chw.reshape(n, IMG_C, IMG_H, IMG_W).transpose(0, 2, 3, 1)
    hwc_rt = decode_images(buf, opts)
    if not np.array_equal(hwc_src, hwc_rt):
        bad = int(np.argmax((hwc_src != hwc_rt).reshape(n, -1).any(axis=1)))
        raise AssertionError(f"round-trip mismatch, first bad image index {bad}")
    out.append("round-trip OK: decode(convert(x)) == x for every image (exact)")

    # Padding channels must be untouched.
    enc = buf.reshape(n, IMG_H, IMG_W, opts.cvec, opts.fmt.bytes_per_element)
    if opts.cvec > IMG_C:
        pad = enc[:, :, :, IMG_C:, :]
        if not np.all(pad == opts.pad_byte):
            raise AssertionError("padding channels are not uniform")
        out.append(f"padding OK: channels {IMG_C}..{opts.cvec - 1} all 0x{opts.pad_byte:02x}")

    # Hand-checked scalar probes on image 0: byte offset from first principles.
    bpe = opts.fmt.bytes_per_element
    for (h, w, c) in [(0, 0, 0), (0, 0, 2), (5, 17, 1), (31, 31, 2)]:
        off = ((h * IMG_W + w) * opts.cvec + c) * bpe
        src = rows_chw[0, c * IMG_H * IMG_W + h * IMG_W + w]
        got = buf[0, off:off + bpe].tobytes()
        if opts.fmt.name == "fp16":
            want = np.array([src], dtype="<f2").tobytes()
        elif opts.fmt.name == "int8":
            want = np.array([int(src) + opts.int8_zero_point_offset],
                            dtype=np.int8).tobytes()
        else:
            want = bytes([int(src)])
        if got != want:
            raise AssertionError(
                f"probe (h={h},w={w},c={c}) off={off}: got {got.hex()} want {want.hex()}")
    out.append("probe OK: 4 hand-computed (h,w,c) byte offsets match")

    # Value range.
    out.append(f"range OK: source u8 min={int(rows_chw.min())} max={int(rows_chw.max())}")
    if opts.fmt.name == "fp16":
        halves = np.ascontiguousarray(enc[:, :, :, :IMG_C, :]).reshape(-1).view("<f2")
        if not np.array_equal(halves.astype(np.float64), np.round(halves.astype(np.float64))):
            raise AssertionError("fp16 values are not integral")
        out.append(f"fp16 OK: all elements integral, min={float(halves.min())} "
                   f"max={float(halves.max())} (exact, no rounding)")
    return out


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
def cmd_extract(args) -> int:
    tar_path = os.path.abspath(args.tar)
    dest = os.path.abspath(args.dest)
    os.makedirs(dest, exist_ok=True)

    size = os.path.getsize(tar_path)
    md5 = _md5(tar_path)
    sha = _sha256_file(tar_path)
    ok = (md5 == ARCHIVE_MD5)
    print(f"archive : {tar_path}")
    print(f"  size  : {size} B")
    print(f"  md5   : {md5}  ({'MATCH published ' + ARCHIVE_MD5 if ok else 'MISMATCH, expected ' + ARCHIVE_MD5})")
    print(f"  sha256: {sha}")
    if not ok and not args.force:
        raise SystemExit("archive md5 mismatch; re-download (or pass --force)")

    members = args.members or ["test_batch", "batches.meta"]
    results = {}
    with tarfile.open(tar_path, "r:gz") as tf:
        names = {os.path.basename(m.name): m for m in tf.getmembers() if m.isfile()}
        outdir = os.path.join(dest, "cifar-10-batches-py")
        os.makedirs(outdir, exist_ok=True)
        for m in members:
            if m not in names:
                raise SystemExit(f"member {m} not present in archive")
            blob = tf.extractfile(names[m]).read()
            got = _md5_bytes(blob)
            want = MEMBER_MD5.get(m)
            out = os.path.join(outdir, m)
            with open(out, "wb") as f:
                f.write(blob)
            results[m] = {
                "path": out.replace("\\", "/"),
                "bytes": len(blob),
                "md5": got,
                "md5_published": want,
                "md5_ok": (want is None or got == want),
                "sha256": hashlib.sha256(blob).hexdigest(),
            }
            flag = "OK" if results[m]["md5_ok"] else "MISMATCH"
            print(f"extracted {m}: {len(blob)} B  md5={got} [{flag}]  sha256={results[m]['sha256']}")

    manifest = {
        "archive": {"path": tar_path.replace("\\", "/"), "bytes": size,
                    "md5": md5, "md5_published": ARCHIVE_MD5, "md5_ok": ok,
                    "sha256": sha,
                    "url": "https://www.cs.toronto.edu/~kriz/cifar-10-python.tar.gz"},
        "members": results,
    }
    mpath = os.path.join(dest, "cifar10_source_manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"manifest: {mpath}")
    return 0


def _select_indices(args, labels: np.ndarray) -> np.ndarray:
    n_total = labels.shape[0]
    if args.subset == "all":
        return np.arange(n_total, dtype=np.int64)
    if args.subset == "range":
        start = args.start
        count = args.count if args.count is not None else n_total - start
        if start < 0 or start + count > n_total:
            raise SystemExit(f"range [{start}, {start + count}) out of bounds")
        return np.arange(start, start + count, dtype=np.int64)
    if args.subset == "perf":
        if not args.perf_idxs:
            raise SystemExit("--subset perf requires --perf-idxs")
        idxs = np.load(args.perf_idxs).astype(np.int64)
        if idxs.ndim != 1:
            raise SystemExit(f"perf idxs has shape {idxs.shape}")
        return idxs
    raise SystemExit(f"unknown subset {args.subset}")


def cmd_convert(args) -> int:
    fmt = ELEMENT_FORMATS[args.format]
    opts = ConvertOptions(fmt=fmt, cvec=args.cvec,
                          pad_byte=args.pad_byte,
                          int8_zero_point_offset=args.int8_zero_point)

    data, labels, filenames = load_test_batch(args.cifar_dir)
    idxs = _select_indices(args, labels)
    rows = data[idxs]
    sel_labels = labels[idxs]
    sel_names = filenames[idxs]

    out_dir = os.path.abspath(args.out_dir)
    os.makedirs(out_dir, exist_ok=True)

    print(f"format  : {fmt.name} ({fmt.description})")
    print(f"cvec    : {opts.cvec}   pad byte 0x{opts.pad_byte:02x}")
    print(f"per img : {opts.bytes_per_image} B "
          f"({IMG_H * IMG_W * opts.cvec} elements)")
    print(f"subset  : {args.subset}  n={len(idxs)}")

    buf = convert_images(rows, opts)

    notes: List[str] = []
    if args.verify_mapping:
        note = verify_mapping_csv(args.verify_mapping, opts)
        print(note)
        notes.append(note)
    if args.selftest:
        for line in selftest(rows, buf, opts):
            print("selftest:", line)
            notes.append(line)

    stem = f"cifar10_test_{args.subset}_{fmt.name}_cvec{opts.cvec}"
    bin_path = os.path.join(out_dir, stem + ".bin")
    with open(bin_path, "wb") as f:
        f.write(buf.tobytes())

    # Per-image SHA-256 of the converted buffer.
    per_sha = [hashlib.sha256(buf[i].tobytes()).hexdigest() for i in range(buf.shape[0])]
    sha_path = os.path.join(out_dir, stem + "_sha256.txt")
    with open(sha_path, "w", newline="\n") as f:
        f.write("# seq\tcifar_test_index\tlabel\tclass\tfilename\tsha256_of_converted_buffer\n")
        for s, (ci, lb, nm, sh) in enumerate(zip(idxs, sel_labels, sel_names, per_sha)):
            f.write(f"{s}\t{int(ci)}\t{int(lb)}\t{CLASS_NAMES[int(lb)]}\t{nm}\t{sh}\n")

    labels_npy = os.path.join(out_dir, stem + "_labels.npy")
    np.save(labels_npy, sel_labels.astype(np.uint8))
    idx_npy = os.path.join(out_dir, stem + "_indices.npy")
    np.save(idx_npy, idxs.astype(np.int32))

    labels_csv = os.path.join(out_dir, stem + "_labels.csv")
    with open(labels_csv, "w", newline="\n") as f:
        f.write("seq,cifar_test_index,filename,num_classes,label\n")
        for s, (ci, lb, nm) in enumerate(zip(idxs, sel_labels, sel_names)):
            f.write(f"{s},{int(ci)},{nm},10,{int(lb)}\n")

    # Eyeball verification: decode straight back out of the converted buffer,
    # so a layout bug would show up as a scrambled/false-colour image.
    ppm_paths: List[str] = []
    png_paths: List[str] = []
    if args.ppm:
        pdir = os.path.join(out_dir, "samples")
        os.makedirs(pdir, exist_ok=True)
        k = min(args.ppm, buf.shape[0])
        dec = decode_images(buf[:k], opts)
        for i in range(k):
            stem_i = f"{i:04d}_idx{int(idxs[i])}_{CLASS_NAMES[int(sel_labels[i])]}"
            p = os.path.join(pdir, stem_i + ".ppm")
            write_ppm(p, dec[i])
            ppm_paths.append(p.replace("\\", "/"))
            png = write_png(os.path.join(pdir, stem_i + ".png"), dec[i])
            if png:
                png_paths.append(png.replace("\\", "/"))
            print(f"sample  : {p}{' + .png' if png else ' (no Pillow, PPM only)'}  "
                  f"(label {int(sel_labels[i])} "
                  f"{CLASS_NAMES[int(sel_labels[i])]}, {sel_names[i]})")

    manifest = {
        "source": {
            "cifar_dir": os.path.abspath(args.cifar_dir).replace("\\", "/"),
            "batch": "test_batch",
            "batch_md5": MEMBER_MD5["test_batch"],
        },
        "subset": {
            "mode": args.subset,
            "count": int(len(idxs)),
            "perf_idxs": (os.path.abspath(args.perf_idxs).replace("\\", "/")
                          if args.perf_idxs else None),
            "start": args.start if args.subset == "range" else None,
            "label_histogram": {CLASS_NAMES[c]: int((sel_labels == c).sum())
                                for c in range(10)},
        },
        "layout": {
            "element_format": fmt.name,
            "bytes_per_element": fmt.bytes_per_element,
            "cvec": opts.cvec,
            "pad_byte": opts.pad_byte,
            "int8_zero_point_offset": opts.int8_zero_point_offset,
            "height": IMG_H, "width": IMG_W, "data_channels": IMG_C,
            "element_index": "(h*32 + w)*cvec + c",
            "byte_index": "((h*32 + w)*cvec + c)*bytes_per_element",
            "bytes_per_image": opts.bytes_per_image,
            "logical_source_layout": "CHW (raw CIFAR-10 batch row, 1024R+1024G+1024B)",
        },
        "outputs": {
            "images_bin": bin_path.replace("\\", "/"),
            "images_bin_bytes": os.path.getsize(bin_path),
            "images_bin_sha256": _sha256_file(bin_path),
            "per_image_sha256": sha_path.replace("\\", "/"),
            "per_image_sha256_sha256": _sha256_file(sha_path),
            "labels_npy": labels_npy.replace("\\", "/"),
            "labels_npy_sha256": _sha256_file(labels_npy),
            "labels_csv": labels_csv.replace("\\", "/"),
            "labels_csv_sha256": _sha256_file(labels_csv),
            "indices_npy": idx_npy.replace("\\", "/"),
            "indices_npy_sha256": _sha256_file(idx_npy),
            "ppm_samples": ppm_paths,
            "png_samples": png_paths,
        },
        "validation": notes,
    }
    mpath = os.path.join(out_dir, stem + "_manifest.json")
    with open(mpath, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"bin     : {bin_path}  ({manifest['outputs']['images_bin_bytes']} B, "
          f"sha256 {manifest['outputs']['images_bin_sha256']})")
    print(f"sha list: {sha_path}")
    print(f"labels  : {labels_npy} / {labels_csv}")
    print(f"indices : {idx_npy}")
    print(f"manifest: {mpath}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    pe = sub.add_parser("extract", help="verify checksums and extract batch files")
    pe.add_argument("--tar", required=True)
    pe.add_argument("--dest", required=True)
    pe.add_argument("--members", nargs="*", default=None)
    pe.add_argument("--force", action="store_true")
    pe.set_defaults(func=cmd_extract)

    pc = sub.add_parser("convert", help="convert images to DLA DDR input buffers")
    pc.add_argument("--cifar-dir", required=True,
                    help="directory containing test_batch")
    pc.add_argument("--out-dir", required=True)
    pc.add_argument("--format", choices=sorted(ELEMENT_FORMATS), default="fp16")
    pc.add_argument("--cvec", type=int, default=8,
                    help="channel vector (arch c_vector); 8 for the current design")
    pc.add_argument("--pad-byte", type=lambda s: int(s, 0), default=0,
                    help="fill byte for channels 3..cvec-1 (default 0)")
    pc.add_argument("--int8-zero-point", type=int, default=-128,
                    help="added to the uint8 value for --format int8")
    pc.add_argument("--subset", choices=["all", "perf", "range"], default="perf")
    pc.add_argument("--perf-idxs", default=None,
                    help="MLPerf Tiny perf_samples_idxs.npy (for --subset perf)")
    pc.add_argument("--start", type=int, default=0)
    pc.add_argument("--count", type=int, default=None)
    pc.add_argument("--ppm", type=int, default=0,
                    help="dump N sample images (PPM always; PNG too if Pillow exists)")
    pc.add_argument("--selftest", action="store_true")
    pc.add_argument("--verify-mapping", default=None,
                    help="AI Suite input_transform_mapping_*.csv to cross-check")
    pc.set_defaults(func=cmd_convert)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
