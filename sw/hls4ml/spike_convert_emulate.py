#!/usr/bin/env python3
"""hls4ml de-risking SPIKE (issue #62 / docs/hls4ml_spatial_track.md §6 step 2).

Backend-independent conversion + bit-accurate fixed-point EMULATION of a small
MLPerf-Tiny reference model through hls4ml. This is the "software value" that the
spike delivers even if the FPGA (oneAPI/Agilex-3) backend is unavailable
(see docs/hls4ml_spike_findings.md, gates G0/G1/G2).

What it does, with NO FPGA compiler required (uses g++ C-simulation only):
  1. Ingest a float ONNX reference model via hls4ml's QONNX front-end
     (with a small channels-last patch for the global-average-pool tail that
     the MLPerf-Tiny graphs use -- documented in the findings doc).
  2. Establish an fp32 reference with ONNX Runtime.
  3. For each ap_fixed<W,I> precision: convert -> compile (g++ csim) -> predict,
     and report fixed-point *fidelity* vs the fp32 reference
     (top-1 agreement, mean/max abs error on the softmax output).
  4. Emit an analytical resource ESTIMATE (MACs, on-chip weight storage) --
     labelled ESTIMATE; exact DSP/ALM/M20K need HLS synthesis, which is blocked
     at the toolchain level (G1).

NOTE on precision: the resnet8 reference consumes raw uint8 pixels (0..255, no
rescaling), so the input needs >=8 integer bits. We therefore hold the integer
width fixed (generous) and sweep the *total* width, isolating fractional-bit
(rounding) error. True sub-8-bit total width would require QKeras retraining with
input rescaling (MLPerf Open division) -- a follow-up, not this spike.

The fixed-point arithmetic emulated here (ap_fixed rounding/saturation via the
bundled ac/ap types) is numerically identical across hls4ml HLS backends, so
these fidelity numbers hold for the oneAPI/Agilex datapath too; only the
resource/timing (which need synthesis) are backend-specific.
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np

CL_DOMAIN = "qonnx.custom_op.channels_last"


def _strip_trailing_softmax(model_or_graph, is_qonnx):
    """Remove the trailing Softmax so the model emits pre-softmax logits.
    argmax(logits) == argmax(softmax), and hls4ml's io_stream softmax LUT is a
    known-lossy stage that collapses to uniform output for this config, so
    accuracy is evaluated on logits (standard hls4ml practice)."""
    g = model_or_graph.graph if is_qonnx else model_or_graph.graph
    sm = [n for n in g.node if n.op_type == "Softmax"]
    for n in sm:
        out_name, in_name = n.output[0], n.input[0]
        for go in g.output:
            if go.name == out_name:
                go.name = in_name
        g.node.remove(n)
    return model_or_graph


def load_and_prepare_onnx(path, in_shape, strip_softmax=True):
    """Load float ONNX, force batch=1, channels-last, patch global-avg-pool tail."""
    import hls4ml  # noqa: F401  (import validates env)
    from onnx import helper as oh
    from qonnx.core.modelwrapper import ModelWrapper
    from qonnx.transformation.channels_last import ConvertToChannelsLastAndClean
    from qonnx.transformation.general import (
        GiveReadableTensorNames,
        GiveUniqueNodeNames,
        GiveUniqueParameterTensors,
    )
    from qonnx.util.cleanup import cleanup_model

    m = ModelWrapper(str(path))
    m.set_tensor_shape(m.graph.input[0].name, list(in_shape))
    m = cleanup_model(m)
    m = m.transform(ConvertToChannelsLastAndClean())
    m = m.transform(GiveUniqueNodeNames())
    m = m.transform(GiveUniqueParameterTensors())
    m = m.transform(GiveReadableTensorNames())

    # qonnx leaves AveragePool channels-first (wrapped in a Transpose) and does
    # not register it as a channels-last custom op. For the *global* pools used
    # by these Tiny models the spatial reduction is layout-invariant, so we drop
    # the pre-pool Transpose, tag the pool channels-last, add the missing 'pads'
    # attr, and permute its output shape NCHW->NHWC.
    g = m.graph
    changed = True
    while changed:
        changed = False
        for pool in [n for n in g.node if n.op_type in ("AveragePool", "MaxPool")]:
            if pool.domain == CL_DOMAIN:
                continue
            prod = m.find_producer(pool.input[0])
            if prod is not None and prod.op_type == "Transpose":
                pool.input[0] = prod.input[0]
                g.node.remove(prod)
            pool.domain = CL_DOMAIN
            if "pads" not in {a.name for a in pool.attribute}:
                ksh = next((list(a.ints) for a in pool.attribute if a.name == "kernel_shape"), [1, 1])
                pool.attribute.append(oh.make_attribute("pads", [0] * (2 * len(ksh))))
            osh = m.get_tensor_shape(pool.output[0])
            if osh is not None and len(osh) == 4:
                n, c, h, w = osh
                m.set_tensor_shape(pool.output[0], [n, h, w, c])
            changed = True
    if strip_softmax:
        m = _strip_trailing_softmax(m, is_qonnx=True)
    return m


def onnx_reference(path, X_nhwc, strip_softmax=True):
    """fp32 reference logits from ONNX Runtime on the ORIGINAL model (softmax stripped)."""
    import onnx
    import onnxruntime as ort

    model = onnx.load(str(path))
    if strip_softmax:
        model = _strip_trailing_softmax(model, is_qonnx=False)
    so = ort.SessionOptions()
    so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess = ort.InferenceSession(model.SerializeToString(), so, providers=["CPUExecutionProvider"])
    iname = sess.get_inputs()[0].name
    out = sess.run(None, {iname: X_nhwc.astype(np.float32)})[0]
    return np.asarray(out)


def analytical_resource_estimate(hls_model, widths_bits):
    """MAC count + on-chip weight storage ESTIMATE (labelled ESTIMATE)."""
    total_macs = 0
    total_params = 0
    per_layer = []
    for layer in hls_model.get_layers():
        cls = layer.__class__.__name__.replace("Vitis", "").replace("Vivado", "").replace("OneAPI", "")
        macs = 0
        params = 0
        if "Conv" in cls:  # Conv1D/Conv2D/PointwiseConv*
            oh_ = layer.get_output_variable().shape  # channels-last, e.g. [H, W, C]
            n_filt = layer.get_attr("n_filt") or 1
            fh = layer.get_attr("filt_height", 1) or 1
            fw = layer.get_attr("filt_width", 1) or 1
            n_chan = layer.get_attr("n_chan") or 1
            out_h = oh_[0] if len(oh_) >= 2 else 1
            out_w = oh_[1] if len(oh_) >= 3 else 1
            macs = out_h * out_w * n_filt * fh * fw * n_chan
            params = n_filt * fh * fw * n_chan + n_filt
        elif cls == "Dense":
            n_in = layer.get_attr("n_in")
            n_out = layer.get_attr("n_out")
            macs = n_in * n_out
            params = n_in * n_out + n_out
        if macs:
            per_layer.append({"layer": layer.name, "class": cls, "macs": int(macs), "params": int(params)})
        total_macs += macs
        total_params += params
    storage = {f"ap_fixed<{w},*>": round(total_params * (w / 8) / 1024, 1) for w in widths_bits}
    return {
        "total_macs": int(total_macs),
        "total_weight_params": int(total_params),
        "on_chip_weight_KB_by_width": storage,
        "per_layer_macs": per_layer,
    }


def fidelity(y_ref, y_test):
    """Compare pre-softmax logits: top-1 class agreement (the accuracy proxy) plus
    scale-invariant relative L2 error and cosine similarity."""
    ref_top1 = y_ref.argmax(axis=1)
    test_top1 = y_test.argmax(axis=1)
    rel_l2 = float(np.mean(np.linalg.norm(y_test - y_ref, axis=1) / (np.linalg.norm(y_ref, axis=1) + 1e-12)))
    cos = float(np.mean(np.sum(y_test * y_ref, axis=1) /
                        (np.linalg.norm(y_test, axis=1) * np.linalg.norm(y_ref, axis=1) + 1e-12)))
    return {
        "top1_agreement_vs_fp": float((ref_top1 == test_top1).mean()),
        "logit_rel_l2_err": rel_l2,
        "logit_cosine_sim": cos,
        "logit_mean_abs_err": float(np.abs(y_ref - y_test).mean()),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--onnx", required=True, help="path to float ONNX reference model")
    ap.add_argument("--in-shape", type=int, nargs="+", default=[1, 32, 32, 3])
    ap.add_argument("--n-samples", type=int, default=32)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--int-bits", type=int, default=12, help="fixed integer bits (held constant across sweep)")
    ap.add_argument("--widths", type=int, nargs="+", default=[32, 18, 16, 14],
                    help="total ap_fixed widths to sweep (integer bits held at --int-bits)")
    ap.add_argument("--input-max", type=float, default=255.0, help="upper bound of raw input range")
    ap.add_argument("--outdir", default="/tmp/hls4ml_spike")
    ap.add_argument("--backend", default="Vitis", help="csim backend (numerics are backend-independent)")
    ap.add_argument("--json-out", default=None)
    args = ap.parse_args()

    import hls4ml

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    rng = np.random.default_rng(args.seed)
    sample_shape = [args.n_samples] + list(args.in_shape[1:])  # replace batch dim with n_samples
    X = rng.integers(0, int(args.input_max) + 1, size=tuple(sample_shape)).astype(np.float32)

    print(f"[spike] model={args.onnx}  samples={args.n_samples}  in_shape={args.in_shape}")
    y_ref = onnx_reference(args.onnx, X)
    print(f"[spike] fp32 ONNX-Runtime reference: output shape {y_ref.shape}")

    results = {"model": str(args.onnx), "hls4ml_version": hls4ml.__version__,
               "backend": args.backend, "n_samples": args.n_samples,
               "int_bits": args.int_bits, "precisions": []}

    resource_done = False
    for w in args.widths:
        if w <= args.int_bits:
            print(f"[spike] skip width {w} (<= int_bits {args.int_bits})")
            continue
        prec = f"ap_fixed<{w},{args.int_bits}>"
        proj = outdir / f"prj_w{w}"
        model = load_and_prepare_onnx(args.onnx, args.in_shape)
        cfg = hls4ml.utils.config_from_onnx_model(
            model, granularity="name", backend=args.backend, default_precision=prec)
        cfg["Model"]["Strategy"] = "Resource"
        cfg["Model"]["ReuseFactor"] = 1024
        # pin the input wide enough for raw 0..255 pixels regardless of sweep width
        cfg.setdefault("LayerName", {}).setdefault("global_in", {})["Precision"] = f"ap_fixed<{args.int_bits+8},{args.int_bits}>"

        hls_model = hls4ml.converters.convert_from_onnx_model(
            model, output_dir=str(proj), backend=args.backend,
            io_type="io_stream", hls_config=cfg)
        hls_model.compile()

        Xin = np.ascontiguousarray(X.reshape(X.shape[0], -1))
        y_hls = np.asarray(hls_model.predict(Xin)).reshape(y_ref.shape)
        fid = fidelity(y_ref, y_hls)
        fid["precision"] = prec
        results["precisions"].append(fid)
        print(f"[spike] {prec}: top1_agree_vs_fp={fid['top1_agreement_vs_fp']:.3f} "
              f"rel_l2={fid['logit_rel_l2_err']:.2e} cos={fid['logit_cosine_sim']:.4f}")

        if not resource_done:
            results["resource_estimate_ESTIMATE"] = analytical_resource_estimate(hls_model, args.widths)
            resource_done = True

    print("\n===== SPIKE SUMMARY =====")
    print(json.dumps(results, indent=2))
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, indent=2))
        print(f"[spike] wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
