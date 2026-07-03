"""Tests for scripts/validate_results.py and the shared reslib validation (issue #4)."""

from pathlib import Path

import pytest

import reslib
import validate_results

FIXTURES = Path(__file__).parent / "fixtures"
EXAMPLES = reslib.RESULTS_DIR / "examples"


def test_examples_are_valid():
    """The committed example results must validate — they are templates for humans/AIs."""
    validator = reslib.make_validator()
    example_paths = sorted(EXAMPLES.glob("*.json"))
    assert example_paths, "expected example result JSONs under results/examples/"
    for path in example_paths:
        result = reslib.load_result(path)
        errs = reslib.validate_result(result.data, validator)
        assert errs == [], f"{path} should be valid, got: {errs}"


def test_repo_results_all_valid():
    """`python scripts/validate_results.py` (no args) must pass on the whole repo."""
    n_invalid, _ = validate_results.validate_paths(reslib.iter_result_paths())
    assert n_invalid == 0


def test_schema_invalid_missing_metrics_fails():
    validator = reslib.make_validator()
    data = reslib.load_result(FIXTURES / "invalid_schema_missing_metrics.json").data
    errs = reslib.validate_result(data, validator)
    assert any("metrics" in e for e in errs), errs


def test_crossfield_measured_without_fclk_fails():
    validator = reslib.make_validator()
    data = reslib.load_result(FIXTURES / "invalid_crossfield_measured_no_fclk.json").data
    errs = reslib.validate_result(data, validator)
    assert any("R1" in e and "fclk_mhz" in e for e in errs), errs


def test_crossfield_model_without_arch_fails():
    validator = reslib.make_validator()
    data = reslib.load_result(FIXTURES / "invalid_crossfield_model_no_arch.json").data
    errs = reslib.validate_result(data, validator)
    assert any("R2" in e and "arch_file" in e for e in errs), errs


def test_reference_with_model_does_not_require_arch():
    """A CPU/OpenVINO reference names a model but used no architecture file (R2 exempts it)."""
    data = {
        "kind": "reference",
        "level": "PH2",
        "subject": "dscnn-cpu-int8",
        "date": "2026-07-12",
        "config": {"device": "A3CY100BM16AE7S", "model": "ds-cnn-kws",
                   "tool_versions": {"openvino": "2024.6"}},
        "metrics": {"accuracy_top1": 0.918},
    }
    assert reslib.cross_field_errors(data) == []


def test_broken_json_is_reported_not_skipped():
    n_invalid, messages = validate_results.validate_paths([FIXTURES / "broken_json.json"])
    assert n_invalid == 1
    assert any("could not read/parse" in m for m in messages)


def test_main_returns_nonzero_on_invalid(capsys):
    rc = validate_results.main([str(FIXTURES / "invalid_crossfield_measured_no_fclk.json")])
    assert rc == 1


def test_main_returns_zero_on_examples(capsys):
    rc = validate_results.main([str(EXAMPLES)])
    assert rc == 0


def test_main_no_files_is_ok(tmp_path, capsys):
    rc = validate_results.main([str(tmp_path)])
    assert rc == 0
