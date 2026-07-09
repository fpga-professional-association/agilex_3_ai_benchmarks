"""Tests for scripts/audit_tensor_mode.py (issue #9).

Fixtures (scripts/tests/fixtures/):
  - classic_mode_n1.fit.rpt: a REAL trimmed excerpt from an actual `quartus_sh --flow compile
    l0_tensor_chain -c l0_tensor_chain_n1` run in this session — 5 classic ([A]) DSP blocks, 0
    tensor ([C]) blocks. Real evidence the audit script's FAIL path fires correctly.
  - synthetic_tensor_pass.fit.rpt: hand-authored (NOT a real compile — see its header and
    rtl/microbench/l0_tensor_chain/README.md for why no real tensor-mode report exists on this
    toolchain/device), used only to exercise the PASS path's parsing + comparison logic.
"""

from pathlib import Path

import pytest

import audit_tensor_mode as atm

FIXTURES = Path(__file__).parent / "fixtures"
CLASSIC_N1 = FIXTURES / "classic_mode_n1.fit.rpt"
SYNTHETIC_PASS = FIXTURES / "synthetic_tensor_pass.fit.rpt"


def test_parse_real_classic_mode_report():
    bd = atm.parse_report_file(CLASSIC_N1)
    assert bd.fixed == 5
    assert bd.floating == 0
    assert bd.tensor == 0
    assert bd.total == 5


def test_parse_synthetic_tensor_pass_report():
    bd = atm.parse_report_file(SYNTHETIC_PASS)
    assert bd.fixed == 0
    assert bd.floating == 0
    assert bd.tensor == 8


def test_parse_report_missing_counters_raises():
    with pytest.raises(atm.ReportParseError):
        atm.parse_report("nothing relevant in this text", source="bogus")


def test_audit_fails_on_classic_mode_fallback():
    """The real N=1 classic-mode report must FAIL an audit expecting tensor_count == 1 (or any N>0)
    — this is the exact "silent 10x loss" scenario PLAN §3 LV2 warns about."""
    ok, table = atm.audit([("N1", 1, CLASSIC_N1)])
    assert ok is False
    assert "FAIL" in table
    assert "0" in table  # the actual [C] count


def test_audit_passes_when_tensor_count_matches():
    ok, table = atm.audit([("N8", 8, SYNTHETIC_PASS)])
    assert ok is True
    assert "PASS" in table
    assert "FAIL" not in table


def test_audit_mixed_reports_any_failure_fails_whole_run():
    ok, _ = atm.audit([("N8", 8, SYNTHETIC_PASS), ("N1", 1, CLASSIC_N1)])
    assert ok is False


def test_audit_missing_report_file_is_a_failure_not_a_crash():
    ok, table = atm.audit([("NX", 4, FIXTURES / "does_not_exist.fit.rpt")])
    assert ok is False
    assert "ERROR" in table


@pytest.mark.parametrize("raw,expected", [
    ("N8=path/to.fit.rpt", ("N8", 8, Path("path/to.fit.rpt"))),
    ("N16=16:some/other.rpt", ("N16", 16, Path("some/other.rpt"))),
])
def test_parse_expect_arg(raw, expected):
    assert atm._parse_expect_arg(raw) == expected


def test_parse_expect_arg_no_n_and_no_digits_raises():
    with pytest.raises(Exception):
        atm._parse_expect_arg("label_with_no_digits=some/path.rpt")


def test_main_exits_nonzero_on_classic_mode_fallback(capsys):
    rc = atm.main(["--expect", f"N1={CLASSIC_N1}"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_main_exits_zero_on_matching_synthetic_pass(capsys):
    rc = atm.main(["--report", str(SYNTHETIC_PASS), "--expect-tensor", "8"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "PASS" in out
