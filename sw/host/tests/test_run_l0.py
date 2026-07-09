"""Full run_l0 flow against a mock transport (issue #9), same idea as test_run_bench.py (#17)."""

import sys
from pathlib import Path

import pytest

import l0_golden
import l0_regs as l0
import run_l0
from transport import Transport

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import reslib  # noqa: E402


class MockL0Transport(Transport):
    """In-memory model of l0_tensor_chain's CSR map, driven from sw/host/l0_golden.py so the same
    run_l0.run_microbench() code path that will eventually talk to real hardware is exercised end to
    end, including --verify-golden."""

    def __init__(self, n_blocks: int, n_taps: int = 10):
        self.n_blocks = n_blocks
        self.n_taps = n_taps
        self.reg = {off: 0 for off in l0.REG.values()}
        self._running = False
        self._n_vectors = 0
        self._snap = {"cycles": 0, "done": 0, "checksum": 0}

    def read32(self, offset: int) -> int:
        if offset == l0.REG["N_BLOCKS"]:
            return self.n_blocks
        if offset == l0.REG["CYCLES_LO"]:
            return self._snap["cycles"] & 0xFFFFFFFF
        if offset == l0.REG["CYCLES_HI"]:
            return (self._snap["cycles"] >> 32) & 0xFFFFFFFF
        if offset == l0.REG["DONE_COUNT"]:
            return self._snap["done"]
        if offset == l0.REG["CHECKSUM"]:
            return self._snap["checksum"]
        if offset == l0.REG["STATUS"]:
            return (1 << l0.ST_DONE) if not self._running and self._n_vectors else 0
        return self.reg.get(offset, 0)

    def write32(self, offset: int, value: int) -> None:
        value &= 0xFFFFFFFF
        if offset == l0.REG["CTRL"] and (value & (1 << l0.CTRL_START)):
            self._running = False   # instant-complete mock: "done" the very next STATUS read
            result = l0_golden.run(self.n_blocks, self.n_taps, self._n_vectors)
            self._snap = result
        elif offset == l0.REG["N_VECTORS"]:
            self._n_vectors = value
            self.reg[offset] = value

    def write_block(self, addr: int, data: bytes) -> None:
        raise NotImplementedError("l0_tensor_chain has no HyperRAM/block interface")

    def read_block(self, addr: int, nbytes: int) -> bytes:
        raise NotImplementedError("l0_tensor_chain has no HyperRAM/block interface")


def test_happy_path_metrics():
    t = MockL0Transport(n_blocks=8)
    run = run_l0.run_microbench(t, n_blocks=8, n_vectors=1000, fclk_mhz=59.63, poll_interval=0)
    expected = l0_golden.run(8, 10, 1000)
    assert run["counters"].cycles == expected["cycles"]
    assert run["counters"].checksum == expected["checksum"]
    assert run["counters"].done == 1000
    expected_macs = 1000 * 8 * 10
    assert run["metrics"]["macs_per_dsp_cycle"] == pytest.approx(
        expected_macs / (expected["cycles"] * 8))


def test_verify_golden_passes_when_consistent():
    t = MockL0Transport(n_blocks=8)
    run = run_l0.run_microbench(t, n_blocks=8, n_vectors=1000, fclk_mhz=59.63, poll_interval=0,
                                verify_golden=True)
    assert run["golden_verified"] is True


def test_verify_golden_catches_a_real_mismatch():
    t = MockL0Transport(n_blocks=8)
    # corrupt the checksum the mock would otherwise return, simulating a hardware disagreement
    orig_read32 = t.read32
    def bad_read32(offset):
        v = orig_read32(offset)
        return (v ^ 0xFFFFFFFF) if offset == l0.REG["CHECKSUM"] else v
    t.read32 = bad_read32
    with pytest.raises(RuntimeError, match="does not match"):
        run_l0.run_microbench(t, n_blocks=8, n_vectors=1000, fclk_mhz=59.63, poll_interval=0,
                              verify_golden=True)


def test_n_blocks_mismatch_refused():
    t = MockL0Transport(n_blocks=8)
    with pytest.raises(RuntimeError, match="N_BLOCKS"):
        run_l0.run_microbench(t, n_blocks=16, n_vectors=100, fclk_mhz=59.63, poll_interval=0)


def test_n_vectors_must_be_positive():
    t = MockL0Transport(n_blocks=8)
    with pytest.raises(RuntimeError, match="n_vectors"):
        run_l0.run_microbench(t, n_blocks=8, n_vectors=0, fclk_mhz=59.63, poll_interval=0)


def test_result_json_is_schema_valid():
    t = MockL0Transport(n_blocks=8)
    run = run_l0.run_microbench(t, n_blocks=8, n_vectors=1000, fclk_mhz=59.63, poll_interval=0)
    result = run_l0.build_result(run, fclk_mhz=59.63, date="2026-07-04", subject="l0-tensor-chain-n8")
    validator = reslib.make_validator()
    errs = reslib.validate_result(result, validator)
    assert errs == [], errs
    assert result["kind"] == "measured"
    assert result["level"] == "L0"
    assert result["config"]["utilization"]["dsp_tensor_mode"] == 0
