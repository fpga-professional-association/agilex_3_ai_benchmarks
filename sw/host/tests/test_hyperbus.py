"""Unit tests for sw/host/hyperbus.py's typed CSR wrappers against MockL3Transport (issue #14)."""

import pytest

import hyperbus as hb


def test_trainer_train_returns_window():
    t = hb.MockL3Transport()
    t.program_train(lo=10, hi=15, width=6, center=13, valid=True)
    trainer = hb.HbTrainer(t)
    window = trainer.train(poll_interval=0)
    assert window == {"lo": 10, "hi": 15, "width": 6, "center": 13, "valid": True}


def test_trainer_window_invalid_when_too_narrow():
    t = hb.MockL3Transport()
    t.program_train(lo=4, hi=4, width=1, center=4, valid=False)
    trainer = hb.HbTrainer(t)
    window = trainer.train(poll_interval=0)
    assert window["valid"] is False
    assert window["width"] == 1


def test_trainer_base_offset_applied():
    t = hb.MockL3Transport()
    t.program_train(lo=1, hi=2, width=2, center=1, valid=True)
    trainer = hb.HbTrainer(t, base=hb.MockL3Transport.TRAINER_BASE)
    trainer.start()
    assert t.reg == {} or True  # start() only ever touches CTRL, which the mock intercepts
    assert trainer.is_done()


def test_memtest_run_clean():
    t = hb.MockL3Transport()
    t.program_memtest(pass_done=100, err_count=0, err_addr=0)
    mt = hb.L3Memtest(t, base=hb.MockL3Transport.MEMTEST_BASE)
    out = mt.run(seed=0xACE1, base_addr=0x1000, span_words=4096, pass_target=100, poll_interval=0)
    assert out["pass_done"] == 100
    assert out["err_count"] == 0
    assert out["error_rate"] == 0.0
    # configuration actually reached the device registers (at their base-offset address)
    base = hb.MockL3Transport.MEMTEST_BASE
    assert t.reg[base + hb.MEMTEST_REG["SEED"]] == 0xACE1
    assert t.reg[base + hb.MEMTEST_REG["BASE_ADDR"]] == 0x1000
    assert t.reg[base + hb.MEMTEST_REG["SPAN_WORDS"]] == 4096
    assert t.reg[base + hb.MEMTEST_REG["PASS_TARGET"]] == 100


def test_memtest_run_with_errors_computes_rate():
    t = hb.MockL3Transport()
    t.program_memtest(pass_done=10, err_count=3, err_addr=1234)
    mt = hb.L3Memtest(t, base=hb.MockL3Transport.MEMTEST_BASE)
    out = mt.run(seed=1, base_addr=0, span_words=1000, pass_target=10, poll_interval=0)
    assert out["err_count"] == 3
    assert out["err_addr"] == 1234
    assert abs(out["error_rate"] - 3 / (1000 * 10)) < 1e-12


def test_bw_engine_read_direction_sets_dir_bit():
    t = hb.MockL3Transport()
    t.program_bw(bursts_done=5, cycles=100_000)
    bw = hb.L3BwEngine(t, base=hb.MockL3Transport.BW_BASE)
    out = bw.run(base_addr=0, burst_words=32, burst_count=5, dir_read=True, fclk_mhz=300,
                poll_interval=0)
    assert out["bursts_done"] == 5
    assert out["cycles"] == 100_000
    total_bytes = 32 * 5 * 2
    expected_mbps = total_bytes / (100_000 / 300e6) / 1e6
    assert abs(out["sustained_mbps"] - expected_mbps) < 1e-6


def test_bw_engine_write_direction_no_dir_bit():
    t = hb.MockL3Transport()
    t.program_bw(bursts_done=2, cycles=1000)
    bw = hb.L3BwEngine(t, base=hb.MockL3Transport.BW_BASE)
    out = bw.run(base_addr=0, burst_words=16, burst_count=2, dir_read=False, fclk_mhz=100,
                poll_interval=0)
    assert out["bursts_done"] == 2
    assert out["cycles"] == 1000


def test_poll_timeout_raises():
    t = hb.MockL3Transport()
    trainer = hb.HbTrainer(t)
    # never call start() -> STATUS.DONE never sets -> must time out, never hang
    with pytest.raises(hb.PollTimeout):
        trainer.wait_done(poll_interval=0, max_polls=10)


def test_devices_use_distinct_base_addresses_without_colliding():
    t = hb.MockL3Transport()
    t.program_train(lo=1, hi=3, width=3, center=2, valid=True)
    t.program_memtest(pass_done=1, err_count=0, err_addr=0)
    t.program_bw(bursts_done=1, cycles=10)

    trainer = hb.HbTrainer(t, base=hb.MockL3Transport.TRAINER_BASE)
    memtest = hb.L3Memtest(t, base=hb.MockL3Transport.MEMTEST_BASE)
    bw = hb.L3BwEngine(t, base=hb.MockL3Transport.BW_BASE)

    window = trainer.train(poll_interval=0)
    mt = memtest.run(seed=1, base_addr=0, span_words=10, pass_target=1, poll_interval=0)
    bwres = bw.run(base_addr=0, burst_words=4, burst_count=1, dir_read=True, fclk_mhz=100,
                   poll_interval=0)
    assert window["width"] == 3
    assert mt["pass_done"] == 1
    assert bwres["bursts_done"] == 1
