"""Registry-shape tests (issue #2) -- importing models/__init__.py must not need heavy ML deps."""

from __future__ import annotations

import common
from models import REGISTRY

EXPECTED_MODEL_IDS = {
    "ds-cnn-kws", "resnet8-cifar10", "mobilenetv1-025-vww", "ad-toycar",
    "mobilenetv2-1.0-imagenet", "resnet18-imagenet", "tiny-yolov3",
}


def test_registry_has_all_seven_models():
    assert set(REGISTRY) == EXPECTED_MODEL_IDS


def test_every_entry_is_a_model_spec_with_required_callables():
    for model_id, spec in REGISTRY.items():
        assert isinstance(spec, common.ModelSpec)
        assert spec.model_id == model_id
        assert spec.expected_weight_bytes > 0
        assert spec.metric in ("top1", "auc", "informational")
        assert callable(spec.fetch_checkpoint)
        assert callable(spec.export_onnx)


def test_only_yolo_is_informational_and_has_no_eval():
    informational = [mid for mid, spec in REGISTRY.items() if spec.metric == "informational"]
    assert informational == ["tiny-yolov3"]
    assert REGISTRY["tiny-yolov3"].eval_fp32 is None
    assert REGISTRY["tiny-yolov3"].fetch_dataset is None


def test_evaluable_models_have_fetch_dataset_and_eval_fp32():
    for model_id, spec in REGISTRY.items():
        if model_id == "tiny-yolov3":
            continue
        assert spec.fetch_dataset is not None, model_id
        assert spec.eval_fp32 is not None, model_id
