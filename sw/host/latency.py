"""Latency-histogram → percentile math (issue #17).

`pct_bucket` is byte-for-byte the same algorithm as the #15 scoreboard testbench golden
(sim/scoreboard/tb_scoreboard.sv), so host-computed p50/p99 agree with what the hardware histogram
represents. Cycles → microseconds uses the run's fabric clock (PLAN §6: FPS/latency both derive from
CYCLES_64 and f_clk; throughput and latency are reported separately).
"""

from __future__ import annotations


def pct_bucket(hist: list[int], total: int, p: int) -> int:
    """Smallest bucket whose cumulative count reaches the p-th percentile (matches #15 TB)."""
    if total <= 0:
        return 0
    need = (p * total + 99) // 100          # ceil(p/100 * total)
    cum = 0
    for b, h in enumerate(hist):
        cum += h
        if cum >= need:
            return b
    return len(hist) - 1


def bucket_to_cycles(bucket: int, hist_shift: int) -> float:
    """Representative latency (cycles) for a bucket: its midpoint. Bucket width = 2**hist_shift."""
    width = 1 << hist_shift
    return (bucket + 0.5) * width


def cycles_to_us(cycles: float, fclk_mhz: float) -> float:
    """cycles / (fclk_mhz * 1e6) seconds = cycles / fclk_mhz microseconds."""
    if fclk_mhz <= 0:
        raise ValueError("fclk_mhz must be positive")
    return cycles / fclk_mhz


def percentile_us(hist: list[int], total: int, p: int, hist_shift: int, fclk_mhz: float) -> float:
    return cycles_to_us(bucket_to_cycles(pct_bucket(hist, total, p), hist_shift), fclk_mhz)
