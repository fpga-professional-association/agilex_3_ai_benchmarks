"""Full runner flow against MockTransport, incl. cap-check, mismatch, and schema-valid output (issue #17)."""

import sys
from pathlib import Path

import pytest

import run_bench
import scoreboard as sb
from transport import MockTransport

# reuse the results validator from scripts/
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import reslib  # noqa: E402


def _mock_with_run(done=100, passes=92, cycles=300_000, lat_min=1800, lat_max=9000,
                   hist=None, ramp_step=None):
    t = MockTransport(mem_size=4096)
    if hist is None:
        hist = [0] * sb.HIST_ENTRIES
        hist[10] = done  # all in one bucket for a predictable percentile
    t.program_run(done=done, passes=passes, cycles=cycles, lat_min=lat_min, lat_max=lat_max,
                  hist=hist, ramp_step=ramp_step)
    return t


def test_happy_path_metrics():
    t = _mock_with_run(done=100, passes=92, cycles=300_000)
    run = run_bench.run_benchmark(t, n_records=100, rec_stride=512, rec_base=0, hist_shift=4,
                                  fclk_mhz=300, poll_interval=0)
    m = run["metrics"]
    assert m["n_records"] == 100
    assert abs(m["fps"] - 100 * 300e6 / 300_000) < 1e-6      # = 100000 FPS
    assert abs(m["accuracy_top1"] - 0.92) < 1e-9
    assert abs(m["latency_us_min"] - 1800 / 300) < 1e-9
    assert abs(m["latency_us_max"] - 9000 / 300) < 1e-9


def test_config_written_to_scoreboard():
    t = _mock_with_run()
    run_bench.run_benchmark(t, n_records=100, rec_stride=512, rec_base=0x00100000, hist_shift=4,
                            fclk_mhz=300, poll_interval=0)
    assert t.reg[sb.REG["N_RECORDS"]] == 100
    assert t.reg[sb.REG["REC_STRIDE"]] == 512
    assert t.reg[sb.REG["REC_BASE"]] == 0x00100000
    assert t.reg[sb.REG["HIST_SHIFT"]] == 4


def test_polling_ramps_over_multiple_reads():
    t = _mock_with_run(done=100, ramp_step=25)   # needs ~4 snapshots
    run = run_bench.run_benchmark(t, n_records=100, rec_stride=512, rec_base=0, hist_shift=4,
                                  fclk_mhz=300, poll_interval=0)
    assert run["metrics"]["n_records"] == 100


def test_unlicensed_cap_refused():
    t = _mock_with_run(done=20000)
    with pytest.raises(RuntimeError, match="caps inference"):
        run_bench.run_benchmark(t, n_records=20000, rec_stride=512, rec_base=0, hist_shift=4,
                                fclk_mhz=300, licensed_ip=False, poll_interval=0)


def test_licensed_allows_over_cap():
    t = _mock_with_run(done=20000, passes=20000, cycles=6_000_000)
    run = run_bench.run_benchmark(t, n_records=20000, rec_stride=512, rec_base=0, hist_shift=4,
                                  fclk_mhz=300, licensed_ip=True, poll_interval=0)
    assert run["metrics"]["n_records"] == 20000


def test_done_mismatch_raises():
    # programmed DONE overruns the configured target -> must error, never emit a result
    t = _mock_with_run(done=150)
    with pytest.raises(RuntimeError, match="DONE_COUNT"):
        run_bench.run_benchmark(t, n_records=100, rec_stride=512, rec_base=0, hist_shift=4,
                                fclk_mhz=300, poll_interval=0)


def test_timeout_when_run_stalls():
    t = _mock_with_run(done=5)  # will never reach 100
    with pytest.raises(TimeoutError):
        run_bench.run_benchmark(t, n_records=100, rec_stride=512, rec_base=0, hist_shift=4,
                                fclk_mhz=300, poll_interval=0.001, poll_timeout_s=0.05)


def test_zero_cycles_raises():
    t = _mock_with_run(done=100, cycles=0)
    with pytest.raises(RuntimeError, match="CYCLES_64 is zero"):
        run_bench.run_benchmark(t, n_records=100, rec_stride=512, rec_base=0, hist_shift=4,
                                fclk_mhz=300, poll_interval=0)


def test_emitted_json_is_schema_valid():
    t = _mock_with_run(done=100, passes=90, cycles=300_000)
    run = run_bench.run_benchmark(t, n_records=100, rec_stride=512, rec_base=0, hist_shift=4,
                                  fclk_mhz=300, poll_interval=0)
    result = run_bench.build_result(
        run, fclk_mhz=300, model="ds-cnn-kws", arch_file="models/arch/agilex3_ddrfree.arch",
        date="2026-09-01", subject="dscnn-kws-methodB", feed_method="B",
        tool_versions={"quartus": "25.3", "python": "3.12"})
    validator = reslib.make_validator()
    errs = reslib.validate_result(result, validator)
    assert errs == [], errs
    assert result["kind"] == "measured"
    assert result["config"]["fclk_mhz"] == 300
    assert result["config"]["arch_file"]           # R2 needs it (model set)
