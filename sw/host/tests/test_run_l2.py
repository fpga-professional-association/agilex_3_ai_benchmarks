"""Full run_l2 flow against a mock transport (issue #12), same idea as test_run_l0.py (#9)."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
import l2_golden  # noqa: E402
import reslib  # noqa: E402

import run_l2  # noqa: E402
from transport import Transport  # noqa: E402


class MockL2Transport(Transport):
    """In-memory model of m20k_bw's CSR map, driven from scripts/l2_golden.py so the same
    run_l2.run_microbench() code path that will eventually talk to real hardware is exercised end
    to end, including --verify-golden."""

    def __init__(self, *, num_banks: int, addr_width: int, geometry: str, output_reg: int,
                data_width: int = 32):
        self.num_banks = num_banks
        self.addr_width = addr_width
        self.geometry = geometry
        self.output_reg = output_reg
        self.data_width = data_width
        self._running = False
        self._k = 0
        self._cs_addr = 0
        self._snap = {"cycles": 0, "checksums": [0] * num_banks, "agg_checksum": 0}

    def _dims(self) -> int:
        geom_bit = 1 if self.geometry == "shared" else 0
        return ((self.num_banks & 0xFFFF)
                 | ((self.data_width // 8 & 0xFF) << 16)
                 | ((geom_bit & 0x1) << 24)
                 | ((self.output_reg & 0x1) << 25)
                 | ((self.addr_width & 0x3F) << 26))

    def read32(self, offset: int) -> int:
        R = run_l2.REG
        if offset == R["DIMS"]:
            return self._dims()
        if offset == R["CYCLES_LO"]:
            return self._snap["cycles"] & 0xFFFFFFFF
        if offset == R["CYCLES_HI"]:
            return (self._snap["cycles"] >> 32) & 0xFFFFFFFF
        if offset == R["STATUS"]:
            return (1 << run_l2.ST_DONE) if not self._running and self._k else 0
        if offset == R["CS_DATA"]:
            return self._snap["checksums"][self._cs_addr]
        if offset == R["AGG_CS"]:
            return self._snap["agg_checksum"]
        return 0

    def write32(self, offset: int, value: int) -> None:
        value &= 0xFFFFFFFF
        R = run_l2.REG
        if offset == R["CTRL"] and (value & (1 << run_l2.CTRL_START)):
            self._running = False  # instant-complete mock: "done" the very next STATUS read
            self._snap = l2_golden.run(num_banks=self.num_banks, addr_width=self.addr_width,
                                       k=self._k, geometry=self.geometry,
                                       output_reg=self.output_reg, data_width=self.data_width)
        elif offset == R["K"]:
            self._k = value
        elif offset == R["CS_ADDR"]:
            self._cs_addr = value

    def write_block(self, addr: int, data: bytes) -> None:
        raise NotImplementedError("l2_m20k_bw has no HyperRAM/block interface")

    def read_block(self, addr: int, nbytes: int) -> bytes:
        raise NotImplementedError("l2_m20k_bw has no HyperRAM/block interface")


def test_decode_dims_matches_m20k_bw_pkg_layout():
    # NUM_BANKS=32, WORD_BYTES=4, GEOMETRY=banked(0), OUTPUT_REG=1, ADDR_WIDTH=9
    dims_raw = 32 | (4 << 16) | (0 << 24) | (1 << 25) | (9 << 26)
    d = run_l2.decode_dims(dims_raw)
    assert d == {"num_banks": 32, "word_bytes": 4, "geometry": "banked", "output_reg": 1,
                "addr_width": 9}


def test_compute_gbps_matches_theoretical_at_banked_ceiling():
    # BANKED geometry: cycles = k + drain_cycles; at large k this approaches banks*bytes*fclk
    num_banks, k, word_bytes, fclk_mhz = 32, 1_000_000, 4, 300.0
    cycles = k + 2  # drain_cycles=1+output_reg=2
    gbps = run_l2.compute_gbps(num_banks=num_banks, k=k, word_bytes=word_bytes,
                               fclk_mhz=fclk_mhz, cycles=cycles)
    theoretical = l2_golden.theoretical_gbps(num_banks=num_banks, data_width=word_bytes * 8,
                                             fclk_mhz=fclk_mhz)
    assert gbps == pytest.approx(theoretical, rel=1e-4)


def test_compute_gbps_zero_cycles_is_zero():
    assert run_l2.compute_gbps(num_banks=32, k=100, word_bytes=4, fclk_mhz=300.0, cycles=0) == 0.0


def test_happy_path_banked_outreg():
    t = MockL2Transport(num_banks=4, addr_width=4, geometry="banked", output_reg=1)
    run = run_l2.run_microbench(t, k=20, fclk_mhz=300.0, poll_interval=0)
    expected = l2_golden.run(num_banks=4, addr_width=4, k=20, geometry="banked", output_reg=1)
    assert run["cycles"] == expected["cycles"] == 22
    assert run["bank_checksums"] == expected["checksums"]
    assert run["agg_checksum"] == expected["agg_checksum"] == 0x980CE1D8
    assert run["metrics"]["gbps_aggregate"] > 0


def test_happy_path_shared_roundrobin_same_checksum_slower():
    banked = MockL2Transport(num_banks=4, addr_width=4, geometry="banked", output_reg=1)
    shared = MockL2Transport(num_banks=4, addr_width=4, geometry="shared", output_reg=1)
    run_b = run_l2.run_microbench(banked, k=20, fclk_mhz=300.0, poll_interval=0)
    run_s = run_l2.run_microbench(shared, k=20, fclk_mhz=300.0, poll_interval=0)
    assert run_b["agg_checksum"] == run_s["agg_checksum"]
    assert run_b["cycles"] < run_s["cycles"]
    # same bytes moved, slower elapsed time -> lower achieved GB/s for the anti-pattern geometry
    assert run_s["metrics"]["gbps_aggregate"] < run_b["metrics"]["gbps_aggregate"]


def test_verify_golden_passes_when_consistent():
    t = MockL2Transport(num_banks=4, addr_width=4, geometry="banked", output_reg=1)
    run = run_l2.run_microbench(t, k=20, fclk_mhz=300.0, poll_interval=0, verify_golden=True)
    assert run["golden_verified"] is True


def test_verify_golden_catches_a_real_checksum_mismatch():
    t = MockL2Transport(num_banks=4, addr_width=4, geometry="banked", output_reg=1)
    orig_read32 = t.read32
    def bad_read32(offset):
        v = orig_read32(offset)
        return (v ^ 0xFFFFFFFF) if offset == run_l2.REG["AGG_CS"] else v
    t.read32 = bad_read32
    with pytest.raises(RuntimeError, match="does not match"):
        run_l2.run_microbench(t, k=20, fclk_mhz=300.0, poll_interval=0, verify_golden=True)


def test_k_must_be_positive():
    t = MockL2Transport(num_banks=4, addr_width=4, geometry="banked", output_reg=1)
    with pytest.raises(RuntimeError, match="k must be"):
        run_l2.run_microbench(t, k=0, fclk_mhz=300.0, poll_interval=0)


def test_result_json_is_schema_valid():
    t = MockL2Transport(num_banks=4, addr_width=4, geometry="banked", output_reg=1)
    run = run_l2.run_microbench(t, k=20, fclk_mhz=300.0, poll_interval=0)
    result = run_l2.build_result(run, fclk_mhz=300.0, date="2026-07-09",
                                 subject="l2-m20k-bw-banked-outreg1")
    validator = reslib.make_validator()
    errs = reslib.validate_result(result, validator)
    assert errs == [], errs
    assert result["kind"] == "measured"
    assert result["level"] == "L2"
    assert result["metrics"]["gbps_aggregate"] > 0
