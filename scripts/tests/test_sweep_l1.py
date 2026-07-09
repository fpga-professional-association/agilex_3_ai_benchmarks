"""Unit tests for scripts/sweep_l1.py report parsers (issue #11).

Pure-logic report parsing tested against committed fixture snippets — no Quartus, no hardware
(AGENTS.md: "report parsing must be tested without needing Quartus"). The snippets mirror the exact
line formats produced by Quartus Prime Pro 26.1 for the l1_sweep project (captured from real
compiles).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import sweep_l1  # noqa: E402

# --- fixtures: minimal excerpts in the real report formats ---
STA_MERGED = """\
; Fmax Summary ;
; 50.48 MHz ; 50.48 MHz       ; clk        ;      ; Slow fix6 0C Model              ;
; clk        ; Base ; 3.333  ; 300.03 MHz ; 0.000 ; 1.666 ; { clk } ;
"""

STA_ISOLATED = """\
; Fmax Summary ;
; 61.39 MHz ; 61.39 MHz       ; clk_hot    ;      ; Slow fix6 0C Model              ;
; 58.10 MHz ; 58.10 MHz       ; clk_hot    ;      ; Slow fix6 100C Model            ;
; 155.2 MHz ; 155.2 MHz       ; clk        ;      ; Slow fix6 0C Model              ;
"""

FIT = """\
; DSP Blocks Needed [=A+B+C-D]                                ; 45 / 138           ; 33 %  ;
;     [A] Total Fixed Point DSP Blocks                        ; 45                 ;       ;
;     [B] Total Floating Point DSP Blocks                     ; 0                  ;       ;
;     [C] Total DSP_PRIME Blocks                              ; 0                  ;       ;
; Logic utilization (in ALMs)     ; 578 / 34,000 ( 2 % )                       ;
; M20K blocks                                                 ; 4 / 262            ; 2 %   ;
"""

RETIME = """\
; Retiming Limit Summary ;
; Clock Transfer   ; Limiting Reason        ; Recommendation ;
; Clock Domain clk ; Insufficient Registers ; See the Fast Forward report ;

Critical Chain Summary for Clock Domain clk
Info (18914): The Hyper-Retimer was unable to optimize the design.
"""


def test_parse_fmax_merged_single_corner(tmp_path):
    p = tmp_path / "r.sta.rpt"
    p.write_text(STA_MERGED)
    fmax, detail = sweep_l1.parse_fmax(p, "clk")
    assert fmax == 50.48
    # the 300 MHz SDC-target row has only one MHz field and must not be picked up as an fmax
    assert detail == {"clk": 50.48}


def test_parse_fmax_isolated_picks_hot_worst_corner(tmp_path):
    p = tmp_path / "r.sta.rpt"
    p.write_text(STA_ISOLATED)
    fmax, detail = sweep_l1.parse_fmax(p, "clk_hot")
    assert fmax == 58.10          # worst (lowest) corner for the hot clock
    assert detail["clk"] == 155.2  # cool clock also captured
    assert detail["clk_hot"] == 58.10


def test_parse_fmax_missing_file(tmp_path):
    fmax, detail = sweep_l1.parse_fmax(tmp_path / "nope.sta.rpt", "clk")
    assert fmax is None and detail == {}


def test_parse_fit_dsp_alm_m20k(tmp_path):
    p = tmp_path / "r.fit.rpt"
    p.write_text(FIT)
    d = sweep_l1.parse_fit(p)
    assert d["dsp_fixed"] == 45
    assert d["dsp_tensor"] == 0      # classic mode: no tensor blocks (the #9 finding)
    assert d["alm"] == 578
    assert d["m20k"] == 4


def test_parse_fast_forward_limit_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep_l1, "PROJ", tmp_path)
    (tmp_path / "l1_rX.fit.retime.rpt").write_text(RETIME)
    reasons = sweep_l1.parse_fast_forward("l1_rX")
    assert reasons == ["clk: Insufficient Registers"]


def test_parse_fast_forward_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep_l1, "PROJ", tmp_path)
    assert sweep_l1.parse_fast_forward("l1_missing") == []


def test_grid_is_24_points():
    pts = list(sweep_l1.grid())
    assert len(pts) == 24
    names = {sweep_l1.rev_name(r, c, rl, dl) for r, c, rl, _, dl, _ in pts}
    assert "l1_r3x3_clean_merged" in names
    assert "l1_r5x5_heavy_isolated" in names
    assert len(names) == 24
