"""Percentile math tests — must match the #15 scoreboard TB golden (issue #17)."""

import latency


def test_pct_bucket_matches_tb_algorithm():
    # reference: smallest bucket whose cumulative count reaches ceil(p/100 * total)
    hist = [0, 2, 5, 3, 0, 10, 0, 1]   # total = 21
    total = sum(hist)
    # p50: need = ceil(0.5*21)=11 -> cum 0,2,7,10,10,20 >=11 at bucket 5
    assert latency.pct_bucket(hist, total, 50) == 5
    # p99: need = ceil(0.99*21)=21 -> reached only at bucket 7
    assert latency.pct_bucket(hist, total, 99) == 7
    # p1: need = ceil(0.01*21)=1 -> bucket 1 (first nonzero)
    assert latency.pct_bucket(hist, total, 1) == 1


def test_pct_bucket_empty():
    assert latency.pct_bucket([0] * 64, 0, 50) == 0


def test_cycles_to_us():
    # 300 cycles at 300 MHz = 1 us
    assert latency.cycles_to_us(300, 300) == 1.0
    assert latency.cycles_to_us(150, 300) == 0.5


def test_percentile_us_uses_bucket_midpoint():
    hist = [0] * 64
    hist[10] = 100        # all latencies in bucket 10
    # hist_shift=4 -> width 16, midpoint of bucket 10 = 10.5*16 = 168 cycles
    # at 300 MHz -> 168/300 us
    us = latency.percentile_us(hist, 100, 50, hist_shift=4, fclk_mhz=300)
    assert abs(us - (168 / 300)) < 1e-9
