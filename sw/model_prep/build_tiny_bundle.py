#!/usr/bin/env python3
"""Build MLPerf Tiny reference bundles for the on-board parity gate + accuracy check.

Produces, per model in {ad-toycar, ds-cnn-kws, resnet8-cifar10, mobilenetv1-025-vww}, a
`results/tiny_bundles/<model_id>/` directory the orchestrator's `sw/host/run_tiny_benchmark.py`
loader (`load_bundle`) reads directly: `reference.json` + `records/rec_*.int8` (raw INT8 input
tensors in the exact byte layout the *compiled* CoreDLA IP consumes), each tagged with the
CPU-INT8 reference prediction (`cpu_pred`) and, where available, the MLPerf ground-truth label.

See `docs/tiny_reference_bundles.md` for the full format writeup, the byte-layout evidence, and the
scale/zero-point-drift finding this script's `--fresh-scale` behavior exists to avoid.

CRITICAL: quantization scale/zero-point and input layout are extracted **fresh, live, from the
actual deployed IR** (`quartus/coredla_hyperram_ed/ip/models/<model_id>/<model_id>.xml` -- the
exact model dla_compiler turned into the `.aot` on the board), never from `models/ir/<id>/
quant_manifest.json`. That manifest was found to be *stale* for `ad-toycar` during this work
(scale 0.5054694381414675 in the manifest vs 0.5122092452703738 freshly read from the very same
`.xml` file that both `models/ir/ad-toycar/int8/` and the deployed IP share byte-for-byte) --
exactly the "scale/zero-point drift" failure mode `docs/parity_debugging.md` #3 warns about. Never
special-case around it here; always re-derive.

Usage:
    # small, committed parity set (16 records) -- run once per model, output goes to git:
    python build_tiny_bundle.py --model resnet8-cifar10 \\
        --n-records 16 --out ../../results/tiny_bundles/resnet8-cifar10

    # larger, gitignored accuracy set (regenerate any time, never committed):
    python build_tiny_bundle.py --model resnet8-cifar10 \\
        --n-records 2000 --out ../../results/tiny_bundles/resnet8-cifar10/accuracy_full

    # also (re)computes + writes a fresh results/ph2_<id>-int8-deployed_<date>.json full-test-set
    # accuracy number tied to the *deployed* IR (see module docstring):
    python build_tiny_bundle.py --model resnet8-cifar10 --n-records 16 \\
        --out ../../results/tiny_bundles/resnet8-cifar10 --write-accuracy-result
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import sys
from pathlib import Path

import numpy as np

import common
from models import ad, dscnn, resnet8, vww

_REPO_ROOT = common.REPO_ROOT
sys.path.insert(0, str(_REPO_ROOT / "sw" / "packer"))
import packlib  # noqa: E402  (sw/packer, path added above)

DEPLOYED_IP_DIR = _REPO_ROOT / "quartus" / "coredla_hyperram_ed" / "ip" / "models"

MLPERF_NAME = {
    "ad-toycar": "Anomaly Detection (AD)",
    "ds-cnn-kws": "Keyword Spotting (KWS)",
    "resnet8-cifar10": "Image Classification (IC)",
    "mobilenetv1-025-vww": "Visual Wake Words (VWW)",
}

# Which models need an HWC(dataset-native)->CHW transpose before serializing -- resolved by
# directly diffing the compiled `<model>-rewritten.onnx`'s declared input shape against the
# original tf2onnx export's (see docs/tiny_reference_bundles.md §byte layout, and
# quartus/coredla_hyperram_ed/ip/README.md table in "§3 Where each model's ... IR came from":
# resnet8-cifar10 and mobilenetv1-025-vww were rewritten NCHW-native to satisfy dla_compiler;
# ds-cnn-kws (C=1, layout-invariant) and ad-toycar (flat, no spatial dims) were not.
NCHW_MODELS = {"resnet8-cifar10", "mobilenetv1-025-vww"}
METRIC = {"ad-toycar": "auc", "ds-cnn-kws": "top1", "resnet8-cifar10": "top1",
          "mobilenetv1-025-vww": "top1"}


def deployed_ir_xml(model_id: str) -> Path:
    return DEPLOYED_IP_DIR / model_id / f"{model_id}.xml"


def fresh_quant_params(model_id: str) -> tuple[float, int, dict]:
    """Re-derive (scale, zero_point) from the *deployed* IR's own input FakeQuantize -- live, every
    time, never cached/trusted from a manifest file (see module docstring)."""
    xml = deployed_ir_xml(model_id)
    if not xml.exists():
        raise FileNotFoundError(f"deployed IR missing: {xml} (Track M compile output)")
    fq = common.fakequantize_params_at_input(xml)
    scale, zero_point = common.signed_int8_affine_params(fq["input_low"], fq["input_high"], int(fq["levels"]))
    return scale, zero_point, fq


def make_deployed_predictor(model_id: str):
    """CPU predictor over the exact IR compiled into the .aot, transposing HWC->CHW first for the
    two models the compile rewrote NCHW-native (see NCHW_MODELS)."""
    import openvino as ov

    core = ov.Core()
    model = core.read_model(str(deployed_ir_xml(model_id)))
    compiled = core.compile_model(model, "CPU")
    output = compiled.output(0)
    needs_nchw = model_id in NCHW_MODELS

    def predict(batch):
        arr = np.asarray(batch, dtype=np.float32)
        if needs_nchw:
            arr = np.transpose(arr, (0, 3, 1, 2))
        return compiled(arr)[output]

    return predict


def argmax_logits(row: np.ndarray) -> int:
    return int(np.argmax(row))


def pack_input(model_id: str, arr_float: np.ndarray, scale: float, zero_point: int) -> bytes:
    """Quantize + serialize one sample in the engine-native byte layout (see module docstring)."""
    q = packlib.quantize_int8(np.asarray(arr_float, dtype=np.float32), scale, zero_point)
    if model_id in NCHW_MODELS:
        q = np.transpose(q, (2, 0, 1))  # dataset-native HWC -> engine-native CHW
    return np.ascontiguousarray(q, dtype=np.int8).tobytes(order="C")


def select_indices(total: int, n: int) -> list[int]:
    """n distinct, deterministically-spread indices over [0, total) (no RNG -- reproducible)."""
    if total <= 0:
        raise ValueError("empty item list")
    n = min(n, total)
    if n == total:
        return list(range(total))
    idx = np.linspace(0, total - 1, num=n)
    return sorted(set(int(round(x)) for x in idx))[:n] or [0]


# --------------------------------------------------------------------------------------------------
# Per-model item listings (lazy: only the SELECTED indices get decoded/feature-extracted).
# --------------------------------------------------------------------------------------------------
def _ad_files(dataset_dir: Path) -> list[tuple[str, int]]:
    toycar_dir = dataset_dir / "toycar" / "dev_data" / "ToyCar"
    files = sorted(glob.glob(str(toycar_dir / "test" / "*.wav")))
    return [(f, 1 if Path(f).name.startswith("anomaly_") else 0) for f in files]


def _dscnn_items(dataset_dir: Path):
    import tensorflow_datasets as tfds

    ds = tfds.load("speech_commands", split="test", data_dir=str(dataset_dir / "speech_commands"))
    return list(ds)  # 4890 examples; materializing is cheap (~9 MB of int16 audio)


def _resnet8_rows(dataset_dir: Path) -> list[dict]:
    import pyarrow.parquet as pq

    table = pq.read_table(dataset_dir / "cifar10" / "test.parquet")
    return table.to_pylist()


def _vww_items(dataset_dir: Path) -> list[tuple[Path, int]]:
    dataset_root = dataset_dir / "vww" / "vw_coco2014_96"
    return vww._eval_file_list(dataset_root)


# --------------------------------------------------------------------------------------------------
# Per-model record builder: (float_tensor, label, cpu_pred_or_None, extra_fields) for one item.
# --------------------------------------------------------------------------------------------------
def _ad_record(item, predict_fn):
    path, label = item
    import tensorflow as _unused  # noqa: F401  (ad._file_to_vector_array needs librosa only, not tf)
    vectors = ad._file_to_vector_array(path)
    if len(vectors) == 0:
        return None
    vec = vectors[0].astype(np.float32)
    recon = predict_fn(vec[np.newaxis, :])[0]
    mse = float(np.mean(np.square(vec - recon)))
    return vec, label, None, {"audio_file": Path(path).name, "frame_index": 0, "cpu_recon_mse": mse}


def _dscnn_record(item, predict_fn):
    import tensorflow as tf

    features = dscnn._mfcc_features(item["audio"], tf)  # (49, 10, 1) float32
    label = int(item["label"].numpy())
    logits = predict_fn(features[np.newaxis, ...])[0]
    return features, label, argmax_logits(logits), {}


def _resnet8_record(row, predict_fn):
    from PIL import Image

    img = np.asarray(Image.open(io.BytesIO(row["img"]["bytes"])).convert("RGB"), dtype=np.float32)
    label = int(row["label"])
    logits = predict_fn(img[np.newaxis, ...])[0]
    return img, label, argmax_logits(logits), {}


def _vww_record(item, predict_fn):
    from PIL import Image

    path, label = item
    with Image.open(path) as im:
        img = np.asarray(im.convert("RGB").resize((vww.IMAGE_SIZE, vww.IMAGE_SIZE)), dtype=np.float32) / 255.0
    logits = predict_fn(img[np.newaxis, ...])[0]
    return img, label, argmax_logits(logits), {}


_ITEM_LISTERS = {
    "ad-toycar": _ad_files,
    "ds-cnn-kws": _dscnn_items,
    "resnet8-cifar10": _resnet8_rows,
    "mobilenetv1-025-vww": _vww_items,
}
_RECORD_BUILDERS = {
    "ad-toycar": _ad_record,
    "ds-cnn-kws": _dscnn_record,
    "resnet8-cifar10": _resnet8_record,
    "mobilenetv1-025-vww": _vww_record,
}


def fetch_dataset_for(model_id: str, dataset_dir: Path) -> None:
    specs = {"ad-toycar": ad, "ds-cnn-kws": dscnn, "resnet8-cifar10": resnet8, "mobilenetv1-025-vww": vww}
    specs[model_id].fetch_dataset(dataset_dir)


# --------------------------------------------------------------------------------------------------
# Bundle assembly
# --------------------------------------------------------------------------------------------------
def build_bundle(model_id: str, dataset_dir: Path, n_records: int, out_dir: Path) -> dict:
    if model_id not in _ITEM_LISTERS:
        raise ValueError(f"unknown model id {model_id!r}; choices: {sorted(_ITEM_LISTERS)}")

    scale, zero_point, fq = fresh_quant_params(model_id)
    predict_fn = make_deployed_predictor(model_id)

    items = _ITEM_LISTERS[model_id](dataset_dir)
    idx = select_indices(len(items), n_records)
    builder = _RECORD_BUILDERS[model_id]

    out_dir = Path(out_dir)
    rec_dir = out_dir / "records"
    rec_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    n_bytes = None
    for k, i in enumerate(idx):
        result = builder(items[i], predict_fn)
        if result is None:
            continue
        arr_float, label, cpu_pred, extra = result
        body = pack_input(model_id, arr_float, scale, zero_point)
        if n_bytes is None:
            n_bytes = len(body)
        elif len(body) != n_bytes:
            raise RuntimeError(f"record {i} packed to {len(body)} B, expected {n_bytes} B (ragged input)")
        fname = f"records/rec_{k:05d}.int8"
        (out_dir / fname).write_bytes(body)
        entry = {"file": fname, "label": int(label)}
        if cpu_pred is not None:
            entry["cpu_pred"] = int(cpu_pred)
        entry.update(extra)
        entries.append(entry)

    manifest = {
        "model_id": model_id,
        "mlperf_benchmark": MLPERF_NAME[model_id],
        "metric": METRIC[model_id],
        "n_records": len(entries),
        "n_input_bytes": n_bytes,
        "quant": {
            "scale": scale,
            "zero_point": zero_point,
            "source": str(deployed_ir_xml(model_id).relative_to(_REPO_ROOT)),
            "fakequantize": fq,
        },
        "layout": ("NCHW (dataset-native HWC transposed to CHW before quantize+serialize)"
                   if model_id in NCHW_MODELS else
                   "flat" if model_id == "ad-toycar" else
                   "native dataset shape (H,W,C); C=1 so NHWC/NCHW are byte-identical"),
        "element_order": "row-major (numpy/C order) INT8",
        "notes": (
            "See docs/tiny_reference_bundles.md for full provenance. cpu_pred is the argmax of the "
            "*deployed* IR's own simulated-INT8 (FakeQuantize) output on this exact input -- not "
            "hardware. ref_output (bit-exact device INT8 output bytes) is deliberately NOT included: "
            "CoreDLA's dla_compiler performs its own final-layer INT8 requantization internally and "
            "that scale is not observable off-hardware (dla_compiler has no simulate/dump-reference "
            "mode -- checked: `dla_compiler --help`). The primary correctness gate is "
            "cpu_pred==hardware-argmax agreement (100% expected); see parity_debugging.md."
        ),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    reference = {
        "model_id": model_id,
        "metric": METRIC[model_id],
        "output_bytes": None,  # filled in by the caller from the deployed IR's output shape
        "records": entries,
    }
    return reference, manifest


def deployed_output_bytes(model_id: str) -> int:
    import openvino as ov

    core = ov.Core()
    model = core.read_model(str(deployed_ir_xml(model_id)))
    shape = model.output(0).get_partial_shape()
    n = 1
    for d in list(shape)[1:]:
        n *= int(d.get_length())
    return n


def write_accuracy_result(model_id: str, dataset_dir: Path, results_dir: Path, date: str) -> Path:
    """Fresh full-test-set accuracy/AUC against the *deployed* IR (see module docstring) -- a
    schema-valid results/ JSON, kind=reference, distinguishing it from the (possibly stale)
    models/ir-based ph2_<id>-int8 numbers eval_int8_cpu.py produces."""
    specs = {"ad-toycar": ad, "ds-cnn-kws": dscnn, "resnet8-cifar10": resnet8, "mobilenetv1-025-vww": vww}
    spec = specs[model_id]
    predict_fn = make_deployed_predictor(model_id)
    outcome = spec.eval_with_predictor(predict_fn, dataset_dir)
    config = {
        "device": "A3CY100BM16AE7S",
        "board": "Arrow AXC3000",
        "model": model_id,
        "quantization": "int8-nncf-ptq",
        "report_paths": [str(deployed_ir_xml(model_id).relative_to(_REPO_ROOT))],
        "tool_versions": common.tool_versions("openvino", "tensorflow"),
    }
    notes = (outcome.get("notes", "") + " Computed against the DEPLOYED IR "
             f"({deployed_ir_xml(model_id).relative_to(_REPO_ROOT)}), i.e. the exact bytes "
             "dla_compiler turned into the .aot on the board -- not models/ir/<id>/int8/, which "
             "can drift (see build_tiny_bundle.py module docstring for the ad-toycar case where it "
             "did). Emitted by sw/model_prep/build_tiny_bundle.py --write-accuracy-result.")
    out_path = results_dir / f"ph2_{model_id}-int8-deployed_{date.replace('-', '')}.json"
    common.write_result(out_path, kind="reference", level="PH2", subject=f"{model_id}-int8-deployed",
                        date=date, plan_ref="§5 table", config=config, metrics=outcome["metrics"],
                        notes=notes)
    return out_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True, choices=sorted(_ITEM_LISTERS))
    ap.add_argument("--n-records", type=int, required=True)
    ap.add_argument("--out", required=True, help="bundle output dir, e.g. results/tiny_bundles/<id>")
    ap.add_argument("--dataset-dir", default=str(common.DATASETS_DIR))
    ap.add_argument("--results-dir", default=str(common.RESULTS_DIR))
    ap.add_argument("--date", default=None)
    ap.add_argument("--write-accuracy-result", action="store_true",
                    help="also run the full test set through the deployed IR and write a fresh "
                         "results/ph2_<id>-int8-deployed_<date>.json (slower: seconds-to-~1min)")
    args = ap.parse_args(argv)

    import datetime
    date = args.date or datetime.date.today().isoformat()

    dataset_dir = Path(args.dataset_dir)
    out_dir = Path(args.out)
    results_dir = Path(args.results_dir)

    print(f"[{args.model}] fetching dataset (if needed) ...")
    fetch_dataset_for(args.model, dataset_dir)

    cpu_int8 = {}
    if args.write_accuracy_result:
        print(f"[{args.model}] computing fresh full-test-set accuracy against the deployed IR ...")
        p = write_accuracy_result(args.model, dataset_dir, results_dir, date)
        data = json.loads(p.read_text())
        metric_key = "accuracy_top1" if METRIC[args.model] == "top1" else "auc"
        cpu_int8 = {"metric_name": metric_key, "value": data["metrics"][metric_key],
                    "results_path": str(p.relative_to(common.REPO_ROOT))}
        print(f"[{args.model}] {metric_key}={data['metrics'][metric_key]} -> wrote {p}")

    print(f"[{args.model}] building {args.n_records}-record bundle -> {out_dir} ...")
    reference, manifest = build_bundle(args.model, dataset_dir, args.n_records, out_dir)
    reference["output_bytes"] = deployed_output_bytes(args.model)
    if cpu_int8:
        reference["cpu_int8"] = cpu_int8
    (out_dir / "reference.json").write_text(json.dumps(reference, indent=2) + "\n")
    print(f"[{args.model}] wrote {out_dir / 'reference.json'} "
          f"({manifest['n_records']} records, {manifest['n_input_bytes']} B/input, "
          f"scale={manifest['quant']['scale']:.6g} zero_point={manifest['quant']['zero_point']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
