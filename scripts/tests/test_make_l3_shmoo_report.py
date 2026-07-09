"""Tests for scripts/make_l3_shmoo_report.py (issue #14)."""

from pathlib import Path

import make_l3_shmoo_report as l3r
import reslib


def _shmoo(hyperbus_mhz, *, width, valid, error_rate, err_count=0, n_records=100,
          date="2026-07-04"):
    return reslib.ResultFile(path=Path(f"/fake/hyperbus-shmoo-{hyperbus_mhz}mhz.json"), data={
        "kind": "measured", "level": "L3", "subject": f"hyperbus-shmoo-{hyperbus_mhz}mhz",
        "date": date, "config": {"device": "A3CY100BM16AE7S", "fclk_mhz": 100,
                                  "hyperbus_mhz": hyperbus_mhz, "tool_versions": {}},
        "metrics": {"window_width_taps": width, "window_center_tap": width // 2,
                   "window_valid": valid, "error_rate": error_rate, "err_count": err_count,
                   "n_records": n_records},
    })


def _sustain(burst_bytes, *, sustained_mbps, efficiency_pct, direction="read",
            hyperbus_mhz=166, date="2026-07-04"):
    return reslib.ResultFile(path=Path(f"/fake/hyperbus-sustained-{burst_bytes}b.json"), data={
        "kind": "measured", "level": "L3", "subject": f"hyperbus-sustained-{burst_bytes}b",
        "date": date, "config": {"device": "A3CY100BM16AE7S", "fclk_mhz": 150,
                                  "hyperbus_mhz": hyperbus_mhz, "tool_versions": {}},
        "metrics": {"sustained_mbps": sustained_mbps, "efficiency_pct": efficiency_pct,
                   "burst_bytes": burst_bytes, "direction": direction},
    })


def test_empty_report_is_graceful():
    text = l3r.build_report([])
    assert "No shmoo points yet" in text
    assert "No sustained-BW points yet" in text


def test_operating_point_needs_margin_of_3():
    rows = [_shmoo(100, width=2, valid=True, error_rate=0.0)]
    op = l3r.choose_operating_point(l3r._shmoo_rows(rows))
    assert op is None  # width 2 accepted (>=2) but doesn't meet the 3-tap operating-point margin


def test_operating_point_picks_highest_clean_with_margin():
    rows = [
        _shmoo(100, width=6, valid=True, error_rate=0.0),
        _shmoo(133, width=4, valid=True, error_rate=0.0),
        _shmoo(166, width=3, valid=True, error_rate=0.0),
    ]
    op = l3r.choose_operating_point(l3r._shmoo_rows(rows))
    assert op["hyperbus_mhz"] == 166.0


def test_operating_point_stops_at_first_unstable_clock():
    rows = [
        _shmoo(100, width=6, valid=True, error_rate=0.0),
        _shmoo(133, width=5, valid=True, error_rate=0.0),
        _shmoo(166, width=1, valid=False, error_rate=None),   # first unstable point
        _shmoo(200, width=6, valid=True, error_rate=0.0),     # stray/partial re-run -- must be ignored
    ]
    op = l3r.choose_operating_point(l3r._shmoo_rows(rows))
    assert op["hyperbus_mhz"] == 133.0   # NOT 200, even though that row looks clean in isolation


def test_operating_point_none_when_all_unstable():
    rows = [_shmoo(100, width=1, valid=False, error_rate=None)]
    op = l3r.choose_operating_point(l3r._shmoo_rows(rows))
    assert op is None


def test_report_marks_operating_point_and_stale_row_unclean():
    rows = [
        _shmoo(100, width=6, valid=True, error_rate=0.0),
        _shmoo(133, width=1, valid=False, error_rate=None),
        _shmoo(200, width=6, valid=True, error_rate=0.0),
    ]
    text = l3r.build_report(rows)
    assert "Chosen operating point: 100 MHz" in text
    # the 200 MHz row must render but be marked not-clean since it's past the unstable point
    lines = [ln for ln in text.splitlines() if ln.startswith("| 200")]
    assert len(lines) == 1
    assert "| no |" in lines[0]


def test_report_includes_sustain_table():
    rows = [_sustain(64, sustained_mbps=180.0, efficiency_pct=78.0),
            _sustain(4096, sustained_mbps=250.0, efficiency_pct=85.0)]
    text = l3r.build_report(rows)
    assert "| 64 | read | 166 | 180 | 78 |" in text
    assert "| 4096 | read | 166 | 250 | 85 |" in text
    assert "never quote the 2xf_HB peak" in text


def test_generate_validates_against_schema(tmp_path):
    # a schema-invalid file must raise, not silently be skipped (matches make_report.py's contract)
    bad_dir = tmp_path / "results"
    bad_dir.mkdir()
    import json
    bad = {"kind": "measured", "level": "L3", "subject": "hyperbus-shmoo-100mhz",
          "date": "2026-01-01", "config": {"device": "A3CY100BM16AE7S", "tool_versions": {}},
          "metrics": {"window_width_taps": 5}}  # measured, missing fclk_mhz -> R1 violation
    (bad_dir / "bad.json").write_text(json.dumps(bad))
    import pytest
    with pytest.raises(ValueError):
        l3r.generate(bad_dir)
