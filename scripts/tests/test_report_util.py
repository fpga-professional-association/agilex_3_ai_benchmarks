"""Unit tests for report_util.py (issue #10) — pure text parsing, no Quartus needed."""

from __future__ import annotations

import pytest

import report_util

FIT_SUMMARY_TEXT = """Fitter Status : Successful - Sun Jul  5 01:58:14 2026
Quartus Prime Version : 26.1.0 Build 110 03/26/2026 SC Pro Edition
Revision Name : w4_m256
Top-level Entity Name : soft_mac_array
Family : Agilex 3
Device : A3CY100BM16AE7S
Timing Models : Final
Power Models : Final
Device Status : Final
Logic utilization (in ALMs) : 1,842 / 34,000 ( 5 % )
Total dedicated logic registers : 9216
Total pins : 2 / 254 ( 1 % )
Total block memory bits : 0 / 5,365,760 ( 0 % )
Total RAM Blocks : 0 / 262 ( 0 % )
Total DSP Blocks : 0 / 276 ( 0 % )
Total GTS Transceiver Channels : 0 / 4 ( 0 % )
"""

STA_RPT_TEXT = """
+---------------------------------------------------------------------------------------------------------------------------+
; Fmax Summary                                                                                                              ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
; Fmax      ; Restricted Fmax ; Clock Name ; Note                                         ; Worst-Case Operating Conditions ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
; 581.4 MHz ; 352.98 MHz      ; clk        ; limit due to minimum pulse width restriction ; Slow fix6 0C Model              ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
This panel reports FMAX for every clock in the design, regardless of the user-specified clock periods.
"""

STA_RPT_MULTI_CORNER_TEXT = """
+---------------------------------------------------------------------------------------------------------------------------+
; Fmax Summary                                                                                                              ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
; Fmax      ; Restricted Fmax ; Clock Name ; Note                                         ; Worst-Case Operating Conditions ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
; 400.0 MHz ; 300.0 MHz       ; clk        ;                                              ; Slow fix6 100C Model            ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
; 410.0 MHz ; 320.0 MHz       ; clk        ;                                              ; Slow fix6 0C Model              ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
; 900.0 MHz ; 850.0 MHz       ; clk        ;                                              ; Fast fix6 0C Model              ;
+-----------+-----------------+------------+----------------------------------------------+---------------------------------+
This panel reports FMAX for every clock in the design, regardless of the user-specified clock periods.
"""


def test_parse_fit_summary(tmp_path):
    p = tmp_path / "w4_m256.fit.summary"
    p.write_text(FIT_SUMMARY_TEXT)
    util = report_util.parse_fit_summary(p)
    assert util.alm_used == 1842
    assert util.alm_total == 34000
    assert util.registers == 9216
    assert util.dsp_used == 0
    assert util.dsp_total == 276
    assert util.m20k_used == 0
    assert util.m20k_total == 262
    assert util.fitter_status.startswith("Successful")


def test_parse_fit_summary_missing_file(tmp_path):
    with pytest.raises(report_util.ReportParseError):
        report_util.parse_fit_summary(tmp_path / "does_not_exist.fit.summary")


def test_parse_fit_summary_malformed(tmp_path):
    p = tmp_path / "bad.fit.summary"
    p.write_text("not a real report\n")
    with pytest.raises(report_util.ReportParseError):
        report_util.parse_fit_summary(p)


def test_parse_fmax_summary(tmp_path):
    p = tmp_path / "w4_m256.sta.rpt"
    p.write_text(STA_RPT_TEXT)
    rows = report_util.parse_fmax_summary(p)
    assert len(rows) == 1
    row = rows[0]
    assert row.clock_name == "clk"
    assert row.fmax_mhz == 581.4
    assert row.restricted_fmax_mhz == 352.98
    assert row.corner == "Slow fix6 0C Model"
    assert "minimum pulse width" in row.note


def test_parse_fmax_summary_missing_table(tmp_path):
    p = tmp_path / "no_table.sta.rpt"
    p.write_text("nothing relevant here\n")
    with pytest.raises(report_util.ReportParseError):
        report_util.parse_fmax_summary(p)


def test_slow_corner_fmax_picks_worst_case(tmp_path):
    p = tmp_path / "multi.sta.rpt"
    p.write_text(STA_RPT_MULTI_CORNER_TEXT)
    rows = report_util.parse_fmax_summary(p)
    assert len(rows) == 3
    # Two "Slow" rows (plain Fmax 400.0, 410.0) and one "Fast" row (900.0, excluded); must return
    # the min of the Slow-corner *plain* Fmax values (not Restricted Fmax — see the function's
    # docstring for why — and not the Fast-corner row).
    assert report_util.slow_corner_fmax_mhz(rows) == 400.0
    assert report_util.slow_corner_fmax_mhz(rows, clock_name="clk") == 400.0
    # restricted=True opts back into the old (clamped) Restricted Fmax column.
    assert report_util.slow_corner_fmax_mhz(rows, restricted=True) == 300.0


def test_slow_corner_fmax_no_match(tmp_path):
    p = tmp_path / "multi.sta.rpt"
    p.write_text(STA_RPT_MULTI_CORNER_TEXT)
    rows = report_util.parse_fmax_summary(p)
    with pytest.raises(report_util.ReportParseError):
        report_util.slow_corner_fmax_mhz(rows, clock_name="not_a_real_clock")


def test_grep_report(tmp_path):
    p = tmp_path / "w4_m256.syn.rpt"
    p.write_text("Info: some line\nInfo: Fractal Synthesis applied to 256 multipliers\nInfo: other\n")
    hits = report_util.grep_report(p, "fractal synthesis")
    assert len(hits) == 1
    assert "256 multipliers" in hits[0]


def test_grep_report_missing_file(tmp_path):
    with pytest.raises(report_util.ReportParseError):
        report_util.grep_report(tmp_path / "missing.rpt", "anything")
