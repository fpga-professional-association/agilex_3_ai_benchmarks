"""Tests for scripts/make_report.py (issue #4)."""

import json
from pathlib import Path

import pytest

import make_report
import reslib

EXAMPLES = reslib.RESULTS_DIR / "examples"


def _load_examples():
    return [reslib.load_result(p) for p in sorted(EXAMPLES.glob("*.json"))]


def test_report_has_a_section_per_level():
    report_dir = reslib.RESULTS_DIR / "reports"
    text = make_report.build_report(_load_examples(), report_dir)
    # examples cover PH0 (estimate) and L0 (measured)
    assert "## L0" in text
    assert "## PH0" in text
    # measured/estimate marks present and distinct
    assert "◆ measured" in text
    assert "◇ estimate" in text


def test_report_links_source_json():
    report_dir = reslib.RESULTS_DIR / "reports"
    text = make_report.build_report(_load_examples(), report_dir)
    # link from results/reports/ up to results/examples/<file>.json
    assert "../examples/measured_l0_tensor_chain.json" in text
    assert "../examples/estimate_dscnn_ph0.json" in text


def test_empty_report_is_graceful():
    text = make_report.build_report([], reslib.RESULTS_DIR / "reports")
    assert "No results yet" in text


def test_generate_raises_on_invalid_input(tmp_path):
    results_dir = tmp_path / "results"
    results_dir.mkdir()
    bad = {"kind": "measured", "level": "L0", "subject": "x", "date": "2026-01-01",
           "config": {"device": "A3CY100BM16AE7S", "tool_versions": {}}}  # measured, no fclk
    (results_dir / "bad.json").write_text(json.dumps(bad))
    with pytest.raises(ValueError):
        make_report.generate(results_dir, tmp_path / "reports" / "summary.md")


def test_check_mode_detects_staleness(tmp_path, capsys):
    out = tmp_path / "summary.md"
    out.write_text("stale content\n")
    rc = make_report.main(["--out", str(out), "--check"])
    assert rc == 1


def test_write_then_check_roundtrips(tmp_path):
    out = tmp_path / "summary.md"
    assert make_report.main(["--out", str(out)]) == 0
    assert out.exists()
    assert make_report.main(["--out", str(out), "--check"]) == 0
