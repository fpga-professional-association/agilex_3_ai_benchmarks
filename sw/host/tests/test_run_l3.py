"""Full L3 runner flow against MockL3Transport: shmoo-point, sustain, and schema-valid output
(issue #14)."""

import sys
from pathlib import Path

import pytest

import hyperbus as hb
import run_l3

# reuse the results validator from scripts/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import reslib  # noqa: E402


def _mock_stable(width=6, center=13, pass_done=100, err_count=0):
    t = hb.MockL3Transport()
    t.program_train(lo=10, hi=10 + width - 1, width=width, center=center, valid=True)
    t.program_memtest(pass_done=pass_done, err_count=err_count, err_addr=0)
    return t


# ---- shmoo-point ----

def test_shmoo_point_happy_path():
    t = _mock_stable()
    run = run_l3.run_shmoo_point(t, span_words=4096, pass_target=100, poll_interval=0)
    assert run["window"]["width"] == 6
    assert run["memtest"]["pass_done"] == 100
    assert run["memtest"]["err_count"] == 0
    assert run["memtest"]["error_rate"] == 0.0


def test_shmoo_point_narrow_window_raises():
    t = _mock_stable(width=1)  # below default min_window=2
    with pytest.raises(RuntimeError, match="too narrow"):
        run_l3.run_shmoo_point(t, span_words=4096, pass_target=100, poll_interval=0)


def test_shmoo_point_invalid_window_raises():
    t = hb.MockL3Transport()
    t.program_train(lo=0, hi=0, width=1, center=0, valid=False)
    t.program_memtest(pass_done=100, err_count=0, err_addr=0)
    with pytest.raises(RuntimeError, match="capture window"):
        run_l3.run_shmoo_point(t, span_words=4096, pass_target=100, poll_interval=0)


def test_shmoo_point_result_is_schema_valid():
    t = _mock_stable()
    run = run_l3.run_shmoo_point(t, span_words=4096, pass_target=100, poll_interval=0)
    result = run_l3.build_shmoo_result(run, hyperbus_mhz=100, fclk_mhz=100, date="2026-07-04")
    validator = reslib.make_validator()
    errs = reslib.validate_result(result, validator)
    assert errs == [], errs
    assert result["kind"] == "measured"
    assert result["level"] == "L3"
    assert result["subject"] == "hyperbus-shmoo-100mhz"
    assert result["config"]["hyperbus_mhz"] == 100
    assert result["metrics"]["window_width_taps"] == 6
    assert result["metrics"]["error_rate"] == 0.0


def test_shmoo_point_result_with_errors():
    t = _mock_stable(err_count=5, pass_done=100)
    run = run_l3.run_shmoo_point(t, span_words=1000, pass_target=100, poll_interval=0)
    result = run_l3.build_shmoo_result(run, hyperbus_mhz=166, fclk_mhz=150, date="2026-07-04")
    assert result["subject"] == "hyperbus-shmoo-166mhz"
    assert result["metrics"]["err_count"] == 5
    assert abs(result["metrics"]["error_rate"] - 5 / (1000 * 100)) < 1e-9


# ---- sustain ----

def test_sustain_happy_path_and_efficiency():
    t = hb.MockL3Transport()
    # 64 B bursts x 1000, @ 166 MHz HyperBus, 2x166=332 MB/s peak
    t.program_bw(bursts_done=1000, cycles=10_000_000)
    run = run_l3.run_sustain(t, burst_bytes=64, burst_count=1000, fclk_mhz=166, hyperbus_mhz=166,
                             poll_interval=0)
    total_bytes = 64 * 1000
    expected_mbps = total_bytes / (10_000_000 / 166e6) / 1e6
    assert abs(run["sustained_mbps"] - expected_mbps) < 1e-6
    expected_eff = 100.0 * expected_mbps / (2 * 166)
    assert abs(run["efficiency_pct"] - expected_eff) < 1e-6


def test_sustain_result_is_schema_valid():
    t = hb.MockL3Transport()
    t.program_bw(bursts_done=500, cycles=5_000_000)
    run = run_l3.run_sustain(t, burst_bytes=1024, burst_count=500, fclk_mhz=166, hyperbus_mhz=166,
                             poll_interval=0)
    result = run_l3.build_sustain_result(run, burst_bytes=1024, burst_count=500, fclk_mhz=166,
                                        hyperbus_mhz=166, dir_read=True, date="2026-07-04")
    validator = reslib.make_validator()
    errs = reslib.validate_result(result, validator)
    assert errs == [], errs
    assert result["subject"] == "hyperbus-sustained-1024b"
    assert result["metrics"]["direction"] == "read"
    assert 0 < result["metrics"]["efficiency_pct"] <= 100


def test_sustain_odd_burst_bytes_rejected():
    t = hb.MockL3Transport()
    with pytest.raises(ValueError, match="even"):
        run_l3.run_sustain(t, burst_bytes=65, burst_count=1, fclk_mhz=100, hyperbus_mhz=100,
                           poll_interval=0)


def test_sustain_never_quotes_peak_as_sustained():
    # sanity: efficiency must be reported relative to 2xf_HB, and sustained_mbps itself must be
    # strictly less than the 2xf_HB peak for any realistic (non-zero-overhead) cycle count.
    t = hb.MockL3Transport()
    t.program_bw(bursts_done=100, cycles=2_000_000)
    run = run_l3.run_sustain(t, burst_bytes=256, burst_count=100, fclk_mhz=166, hyperbus_mhz=166,
                             poll_interval=0)
    peak = 2 * 166
    assert run["sustained_mbps"] < peak
