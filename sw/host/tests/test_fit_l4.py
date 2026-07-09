"""Tests for the L4 overlay-fixed-cost least-squares fit (issue #20), against synthetic/mock latency
data -- no hardware, no OpenVINO. The real silicon sweep is the issue #20 PR's Hardware handoff.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

import fit_l4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import reslib  # noqa: E402

TRUE_OVERHEAD_US = 25.0
TRUE_RATE_MACS_PER_S = 2.0e9          # -> slope_us_per_mac = 1e6 / rate = 5e-4 us/MAC
TRUE_SLOPE_US_PER_MAC = 1.0e6 / TRUE_RATE_MACS_PER_S
SWEEP_MACS = [50_688.0, 304_128.0, 2_036_736.0, 12_708_864.0, 79_736_832.0]  # make_sweep_graphs defaults


def _synthetic_latency(macs: list[float], *, noise_us: float = 0.0, seed: int = 1234) -> list[float]:
    """latency = overhead + macs*slope, plus small deterministic *additive* jitter in microseconds.

    Additive (not proportional-to-value) noise models fixed-scale measurement/histogram-bucket
    jitter -- the realistic case. Proportional noise on the large-MACs points would swamp the
    small-MACs points' contribution to the fit and blow up the extrapolated intercept, which is a
    property of leverage/extrapolation, not something this fit should be graded against.
    """
    rng = random.Random(seed)
    out = []
    for m in macs:
        clean = TRUE_OVERHEAD_US + TRUE_SLOPE_US_PER_MAC * m
        out.append(clean + (rng.random() * 2 - 1) * noise_us)
    return out


def _mock_l4_json(tmp_path: Path, *, subject: str, macs: float, latency_us: float, fclk_mhz=300.0,
                   arch_file="models/arch/AGX3_Performance.arch", feed_method="A") -> Path:
    data = {
        "kind": "measured",
        "level": "L4",
        "subject": subject,
        "date": "2026-07-09",
        "plan_ref": "§7 L4",
        "config": {
            "device": "A3CY100BM16AE7S",
            "board": "Arrow AXC3000",
            "fclk_mhz": fclk_mhz,
            "arch_file": arch_file,
            "model": subject,
            "feed_method": feed_method,
            "macs_per_inference": macs,
            "tool_versions": {"python": "3.12"},
        },
        "metrics": {"latency_us_p50": latency_us, "n_records": 1000},
        "notes": "synthetic mock for issue #20 tests",
    }
    path = tmp_path / f"{subject}.json"
    path.write_text(json.dumps(data, indent=2))
    return path


# --------------------------------------------------------------------------------------------
# t_crit_95
# --------------------------------------------------------------------------------------------

def test_t_crit_95_matches_known_table_values():
    assert fit_l4.t_crit_95(1) == pytest.approx(12.706)
    assert fit_l4.t_crit_95(10) == pytest.approx(2.228)
    assert fit_l4.t_crit_95(30) == pytest.approx(2.042)


def test_t_crit_95_falls_back_to_normal_for_large_dof():
    assert fit_l4.t_crit_95(1000) == pytest.approx(1.96)


def test_t_crit_95_monotonically_decreasing():
    values = [fit_l4.t_crit_95(d) for d in range(1, 31)]
    assert all(a > b for a, b in zip(values, values[1:]))


def test_t_crit_95_rejects_zero_dof():
    with pytest.raises(ValueError):
        fit_l4.t_crit_95(0)


# --------------------------------------------------------------------------------------------
# least_squares_fit / fit_and_check on synthetic data
# --------------------------------------------------------------------------------------------

def test_least_squares_fit_recovers_known_constants_from_clean_data():
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.0)
    result = fit_l4.least_squares_fit(SWEEP_MACS, latency)
    assert result.intercept_us == pytest.approx(TRUE_OVERHEAD_US, abs=1e-6)
    assert result.slope_us_per_mac == pytest.approx(TRUE_SLOPE_US_PER_MAC, rel=1e-6)
    assert result.achieved_macs_per_s == pytest.approx(TRUE_RATE_MACS_PER_S, rel=1e-6)
    assert result.r_squared == pytest.approx(1.0, abs=1e-9)
    assert result.n == 5
    assert result.dof == 3


def test_least_squares_fit_recovers_constants_within_ci_under_small_noise():
    # +-0.5us jitter -- plausible histogram-bucket-scale measurement noise (issue #20 step 3).
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.5, seed=7)
    result = fit_l4.least_squares_fit(SWEEP_MACS, latency)
    assert result.r_squared > 0.98
    ci_lo, ci_hi = result.intercept_ci95_us
    assert ci_lo < ci_hi
    # the 95% CI should contain the true intercept for well-behaved noise at this scale
    assert ci_lo <= TRUE_OVERHEAD_US <= ci_hi
    assert result.intercept_us == pytest.approx(TRUE_OVERHEAD_US, abs=5.0)


def test_least_squares_fit_rejects_mismatched_lengths():
    with pytest.raises(ValueError, match="same length"):
        fit_l4.least_squares_fit([1.0, 2.0], [1.0])


def test_least_squares_fit_rejects_too_few_points():
    with pytest.raises(ValueError, match=">=3"):
        fit_l4.least_squares_fit([1.0, 2.0], [1.0, 2.0])


def test_least_squares_fit_rejects_zero_spread_in_macs():
    with pytest.raises(ValueError, match="identical"):
        fit_l4.least_squares_fit([5.0, 5.0, 5.0], [1.0, 2.0, 3.0])


def test_fit_and_check_passes_on_clean_data():
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.0)
    result = fit_l4.fit_and_check(SWEEP_MACS, latency)
    assert result.r_squared >= fit_l4.R2_MIN


def test_fit_and_check_refuses_low_r2_data():
    # latency uncorrelated with macs (e.g. dominated by jitter, not a fixed-cost+rate model)
    noisy_latency = [40.0, 10.0, 90.0, 5.0, 60.0]
    with pytest.raises(fit_l4.FitQualityError, match="R\\^2"):
        fit_l4.fit_and_check(SWEEP_MACS, noisy_latency)


def test_fit_and_check_refuses_negative_slope():
    # latency decreasing with macs -- physically backwards, must never silently "fit"
    decreasing = [100.0, 90.0, 80.0, 70.0, 60.0]
    with pytest.raises(fit_l4.FitQualityError, match="non-positive"):
        fit_l4.fit_and_check(SWEEP_MACS, decreasing)


def test_overhead_fraction():
    assert fit_l4.overhead_fraction(25.0, 100.0) == pytest.approx(0.25)


def test_overhead_fraction_rejects_nonpositive_latency():
    with pytest.raises(ValueError):
        fit_l4.overhead_fraction(25.0, 0.0)


# --------------------------------------------------------------------------------------------
# load_l4_measurements
# --------------------------------------------------------------------------------------------

def test_load_l4_measurements_reads_macs_and_latency(tmp_path):
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.0)
    paths = [_mock_l4_json(tmp_path, subject=f"l4-sweep-d{i}", macs=m, latency_us=l)
             for i, (m, l) in enumerate(zip(SWEEP_MACS, latency))]
    measurements = fit_l4.load_l4_measurements(paths)
    assert [m.macs for m in measurements] == SWEEP_MACS
    assert [m.latency_us for m in measurements] == pytest.approx(latency)


def test_load_l4_measurements_rejects_method_b(tmp_path):
    p = _mock_l4_json(tmp_path, subject="bad", macs=1000.0, latency_us=10.0, feed_method="B")
    with pytest.raises(ValueError, match="method-A"):
        fit_l4.load_l4_measurements([p])


def test_load_l4_measurements_rejects_wrong_level(tmp_path):
    data = json.loads(_mock_l4_json(tmp_path, subject="bad", macs=1000.0, latency_us=10.0).read_text())
    data["level"] = "L5"
    p = tmp_path / "bad2.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="L5"):
        fit_l4.load_l4_measurements([p])


def test_load_l4_measurements_rejects_missing_macs(tmp_path):
    data = json.loads(_mock_l4_json(tmp_path, subject="bad", macs=1000.0, latency_us=10.0).read_text())
    del data["config"]["macs_per_inference"]
    p = tmp_path / "bad3.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="macs_per_inference"):
        fit_l4.load_l4_measurements([p])


def test_load_l4_measurements_rejects_missing_latency(tmp_path):
    data = json.loads(_mock_l4_json(tmp_path, subject="bad", macs=1000.0, latency_us=10.0).read_text())
    del data["metrics"]["latency_us_p50"]
    p = tmp_path / "bad4.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="latency_us_p50"):
        fit_l4.load_l4_measurements([p])


def test_load_l4_measurements_rejects_estimate_kind(tmp_path):
    data = json.loads(_mock_l4_json(tmp_path, subject="bad", macs=1000.0, latency_us=10.0).read_text())
    data["kind"] = "estimate"
    p = tmp_path / "bad5.json"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="measured"):
        fit_l4.load_l4_measurements([p])


# --------------------------------------------------------------------------------------------
# build_result / schema validity
# --------------------------------------------------------------------------------------------

def test_build_result_is_schema_valid(tmp_path):
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.0)
    paths = [_mock_l4_json(tmp_path, subject=f"l4-sweep-d{i}", macs=m, latency_us=l)
             for i, (m, l) in enumerate(zip(SWEEP_MACS, latency))]
    measurements = fit_l4.load_l4_measurements(paths)
    fit = fit_l4.fit_and_check([m.macs for m in measurements], [m.latency_us for m in measurements])
    result = fit_l4.build_result(measurements, fit, date="2026-07-09")

    validator = reslib.make_validator()
    errors = reslib.validate_result(result, validator)
    assert errors == []
    assert result["metrics"]["overlay_fixed_us"] == pytest.approx(TRUE_OVERHEAD_US, abs=1e-3)
    assert "R^2" in result["notes"]
    assert result["config"]["fclk_mhz"] == 300.0


def test_build_result_notes_drift_when_fclk_disagrees(tmp_path):
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.0)
    paths = [_mock_l4_json(tmp_path, subject=f"l4-sweep-d{i}", macs=m, latency_us=l,
                            fclk_mhz=300.0 if i else 250.0)
             for i, (m, l) in enumerate(zip(SWEEP_MACS, latency))]
    measurements = fit_l4.load_l4_measurements(paths)
    fit = fit_l4.fit_and_check([m.macs for m in measurements], [m.latency_us for m in measurements])
    result = fit_l4.build_result(measurements, fit, date="2026-07-09")
    assert "fclk_mhz" not in result["config"]
    assert "WARNING" in result["notes"]


# --------------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------------

def test_cli_end_to_end_writes_schema_valid_result(tmp_path):
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.0)
    for i, (m, l) in enumerate(zip(SWEEP_MACS, latency)):
        _mock_l4_json(tmp_path, subject=f"l4-sweep-d{i}", macs=m, latency_us=l)
    out = tmp_path / "fit.json"
    rc = fit_l4.main(["--points", str(tmp_path / "*.json"), "--out", str(out), "--date", "2026-07-09"])
    assert rc == 0
    result = json.loads(out.read_text())
    validator = reslib.make_validator()
    assert reslib.validate_result(result, validator) == []
    assert result["metrics"]["overlay_fixed_us"] == pytest.approx(TRUE_OVERHEAD_US, abs=1e-3)


def test_cli_refuses_and_writes_nothing_on_bad_fit(tmp_path):
    noisy_latency = [40.0, 10.0, 90.0, 5.0, 60.0]
    for i, (m, l) in enumerate(zip(SWEEP_MACS, noisy_latency)):
        _mock_l4_json(tmp_path, subject=f"l4-sweep-d{i}", macs=m, latency_us=l)
    out = tmp_path / "fit.json"
    rc = fit_l4.main(["--points", str(tmp_path / "*.json"), "--out", str(out), "--date", "2026-07-09"])
    assert rc != 0
    assert not out.exists()


def test_cli_no_matching_points_fails_loudly(tmp_path):
    out = tmp_path / "fit.json"
    rc = fit_l4.main(["--points", str(tmp_path / "nope-*.json"), "--out", str(out)])
    assert rc != 0
    assert not out.exists()


def test_cli_overhead_fraction_for_tiny_models(tmp_path, capsys):
    latency = _synthetic_latency(SWEEP_MACS, noise_us=0.0)
    for i, (m, l) in enumerate(zip(SWEEP_MACS, latency)):
        _mock_l4_json(tmp_path, subject=f"l4-sweep-d{i}", macs=m, latency_us=l)

    dscnn = tmp_path / "l5_dscnn.json"
    dscnn.write_text(json.dumps({
        "kind": "measured", "level": "L5", "subject": "ds-cnn-kws-methodA", "date": "2026-07-09",
        "config": {"device": "A3CY100BM16AE7S", "fclk_mhz": 300.0,
                   "arch_file": "models/arch/AGX3_Performance.arch", "model": "ds-cnn-kws",
                   "feed_method": "A", "tool_versions": {"python": "3.12"}},
        "metrics": {"latency_us_p50": 50.0},
    }))

    out = tmp_path / "fit.json"
    rc = fit_l4.main(["--points", str(tmp_path / "l4-sweep-*.json"), "--out", str(out),
                       "--date", "2026-07-09", "--overhead-fraction-for", str(dscnn)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "ds-cnn-kws-methodA" in captured.out
    assert "50.0%" in captured.out  # 25us overhead / 50us total
    result = json.loads(out.read_text())
    assert "Overhead fractions" in result["notes"]
