"""Registry of the seven benchmark models (issue #2/#3): MLPerf Tiny four + the >400 KB three.

Each entry is a ``common.ModelSpec`` — the CLI dispatchers (fetch_models.py, fetch_datasets.py,
export_onnx.py, eval_fp32.py, convert_ir.py, quantize_int8.py, eval_int8_cpu.py,
extract_quant_manifest.py) are generic over this registry and never hardcode a model name.
"""

from __future__ import annotations

import functools

import common

from . import ad, dscnn, imagenet, resnet8, vww, yolov3tiny

REGISTRY: dict[str, common.ModelSpec] = {
    dscnn.MODEL_ID: common.ModelSpec(
        model_id=dscnn.MODEL_ID,
        expected_weight_bytes=dscnn.EXPECTED_WEIGHT_BYTES,
        metric="top1",
        fetch_checkpoint=dscnn.fetch_checkpoint,
        fetch_dataset=dscnn.fetch_dataset,
        export_onnx=dscnn.export_onnx,
        eval_fp32=dscnn.eval_fp32,
        calibration_samples=dscnn.calibration_samples,
        eval_with_predictor=dscnn.eval_with_predictor,
    ),
    resnet8.MODEL_ID: common.ModelSpec(
        model_id=resnet8.MODEL_ID,
        expected_weight_bytes=resnet8.EXPECTED_WEIGHT_BYTES,
        metric="top1",
        fetch_checkpoint=resnet8.fetch_checkpoint,
        fetch_dataset=resnet8.fetch_dataset,
        export_onnx=resnet8.export_onnx,
        eval_fp32=resnet8.eval_fp32,
        calibration_samples=resnet8.calibration_samples,
        eval_with_predictor=resnet8.eval_with_predictor,
    ),
    vww.MODEL_ID: common.ModelSpec(
        model_id=vww.MODEL_ID,
        expected_weight_bytes=vww.EXPECTED_WEIGHT_BYTES,
        metric="top1",
        fetch_checkpoint=vww.fetch_checkpoint,
        fetch_dataset=vww.fetch_dataset,
        export_onnx=vww.export_onnx,
        eval_fp32=vww.eval_fp32,
        calibration_samples=vww.calibration_samples,
        eval_with_predictor=vww.eval_with_predictor,
    ),
    ad.MODEL_ID: common.ModelSpec(
        model_id=ad.MODEL_ID,
        expected_weight_bytes=ad.EXPECTED_WEIGHT_BYTES,
        metric="auc",
        fetch_checkpoint=ad.fetch_checkpoint,
        fetch_dataset=ad.fetch_dataset,
        export_onnx=ad.export_onnx,
        eval_fp32=ad.eval_fp32,
        calibration_samples=ad.calibration_samples,
        eval_with_predictor=ad.eval_with_predictor,
    ),
    yolov3tiny.MODEL_ID: common.ModelSpec(
        model_id=yolov3tiny.MODEL_ID,
        expected_weight_bytes=yolov3tiny.EXPECTED_WEIGHT_BYTES,
        metric="informational",
        fetch_checkpoint=yolov3tiny.fetch_checkpoint,
        fetch_dataset=None,
        export_onnx=yolov3tiny.export_onnx,
        eval_fp32=None,
        calibration_samples=None,  # no established preprocessing/accuracy pipeline; see quantize_int8.py
        eval_with_predictor=None,
    ),
}

for _arch, _info in imagenet.ARCHES.items():
    REGISTRY[_info["model_id"]] = common.ModelSpec(
        model_id=_info["model_id"],
        expected_weight_bytes=_info["expected_weight_bytes"],
        metric="top1",
        fetch_checkpoint=functools.partial(imagenet.fetch_checkpoint, arch=_arch),
        fetch_dataset=imagenet.fetch_dataset,
        export_onnx=functools.partial(imagenet.export_onnx, arch=_arch),
        eval_fp32=functools.partial(imagenet.eval_fp32, arch=_arch),
        calibration_samples=functools.partial(imagenet.calibration_samples, arch=_arch),
        eval_with_predictor=functools.partial(imagenet.eval_with_predictor, arch=_arch),
    )
