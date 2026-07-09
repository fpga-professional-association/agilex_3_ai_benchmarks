"""parity_gate.py comparison logic against a mock hw log -- no board, no model (issue #21)."""

from __future__ import annotations

import pytest
from parity_gate import compare_predictions, load_hw_log, main


def test_perfect_match():
    hw = [0, 1, 2, 3, 4]
    cpu = [0, 1, 2, 3, 4]
    result = compare_predictions(hw, cpu)
    assert result["match_rate"] == 1.0
    assert result["n_records"] == 5
    assert result["n_mismatches"] == 0
    assert result["mismatches"] == []


def test_some_mismatches_reported_with_margin():
    hw = [0, 1, 2, 3]
    cpu = [0, 9, 2, 8]  # records 1 and 3 mismatch
    margins = [0.5, 0.01, 0.9, 5.0]
    result = compare_predictions(hw, cpu, margins)
    assert result["match_rate"] == 0.5
    assert result["n_mismatches"] == 2
    assert result["mismatches"] == [
        {"index": 1, "hw_pred": 1, "cpu_pred": 9, "cpu_top2_margin": 0.01},
        {"index": 3, "hw_pred": 3, "cpu_pred": 8, "cpu_top2_margin": 5.0},
    ]


def test_max_mismatches_caps_the_reported_list_but_not_the_rate():
    hw = [0] * 100
    cpu = [1] * 100  # every record mismatches
    result = compare_predictions(hw, cpu, max_mismatches=5)
    assert result["match_rate"] == 0.0
    assert result["n_mismatches"] == 100  # true count, not capped
    assert len(result["mismatches"]) == 5  # reporting is capped


def test_empty_inputs_is_a_vacuous_100_percent_match():
    result = compare_predictions([], [])
    assert result["match_rate"] == 1.0
    assert result["n_records"] == 0


def test_length_mismatch_rejected():
    with pytest.raises(ValueError):
        compare_predictions([0, 1], [0])


def test_margin_key_absent_when_margins_not_given():
    result = compare_predictions([0], [1])
    assert "cpu_top2_margin" not in result["mismatches"][0]


def test_load_hw_log_reads_raw_bytes(tmp_path):
    path = tmp_path / "hw_log.bin"
    path.write_bytes(bytes([7, 8, 9, 255]))
    assert load_hw_log(path, 4) == [7, 8, 9, 255]


def test_load_hw_log_rejects_a_short_file(tmp_path):
    path = tmp_path / "hw_log.bin"
    path.write_bytes(bytes([1, 2]))
    with pytest.raises(ValueError):
        load_hw_log(path, 4)


def test_main_exits_nonzero_below_100_percent(tmp_path, monkeypatch):
    import parity_gate

    monkeypatch.setattr(parity_gate, "run_parity_gate", lambda *a, **k: {
        "match_rate": 0.99, "n_records": 100, "n_mismatches": 1,
        "mismatches": [{"index": 42, "hw_pred": 3, "cpu_pred": 5, "cpu_top2_margin": 0.01}],
    })
    dummy = tmp_path / "x"
    dummy.write_bytes(b"")
    rc = main(["--recimg", str(dummy), "--model-ir", str(dummy),
               "--quant-manifest", str(dummy), "--hw-log", str(dummy)])
    assert rc != 0


def test_main_exits_zero_at_100_percent(tmp_path, monkeypatch):
    import parity_gate

    monkeypatch.setattr(parity_gate, "run_parity_gate", lambda *a, **k: {
        "match_rate": 1.0, "n_records": 100, "n_mismatches": 0, "mismatches": [],
    })
    dummy = tmp_path / "x"
    dummy.write_bytes(b"")
    rc = main(["--recimg", str(dummy), "--model-ir", str(dummy),
               "--quant-manifest", str(dummy), "--hw-log", str(dummy)])
    assert rc == 0
