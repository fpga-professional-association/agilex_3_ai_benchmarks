"""Typed wrappers over the L3 HyperBus capture-trainer + memtest/bandwidth-engine CSR maps
(issue #14).

Offsets mirror docs/hyperbus.md's "Issue #14 addendum" (canonical) and the RTL that implements them
(rtl/hyperbus/hb_trainer.sv, rtl/microbench/l3_memtest/l3_memtest_pkg.sv); test_hyperbus_regs.py
parses that doc to prove these stay in sync, the same convention sw/host/scoreboard.py already uses
against docs/register_map.md. Each class is a thin register-access wrapper over a `Transport`
(sw/host/transport.py) — no register addresses scattered elsewhere (AGENTS.md).

Each engine here has its OWN CSR address block (a real system assigns each a distinct base address
in the Avalon address map, e.g. via Platform Designer); the `base` constructor argument is that
per-instance offset, defaulting to 0 for standalone use/testing.
"""

from __future__ import annotations

from transport import Transport

# ---- hb_trainer (rtl/hyperbus/hb_trainer.sv) ----
TRAINER_REG = {
    "CTRL": 0x00,
    "STATUS": 0x04,
    "WIN_LO": 0x08,
    "WIN_HI": 0x0C,
    "WIN_WIDTH": 0x10,
    "WIN_CENTER": 0x14,
    "NUM_TAPS": 0x18,
    "LAST_TAP": 0x1C,
}
T_CTRL_START = 0
T_ST_BUSY = 0
T_ST_DONE = 1
T_ST_WINDOW_VALID = 2

# ---- l3_memtest_engine (rtl/microbench/l3_memtest/l3_memtest_engine.sv) ----
MEMTEST_REG = {
    "CTRL": 0x00,
    "SEED": 0x04,
    "BASE_ADDR": 0x08,
    "SPAN_WORDS": 0x0C,
    "PASS_TARGET": 0x10,
    "STATUS": 0x14,
    "PASS_DONE": 0x18,
    "ERR_COUNT": 0x1C,
    "ERR_ADDR": 0x20,
}
MT_CTRL_START = 0
MT_ST_BUSY = 0
MT_ST_DONE = 1

# ---- l3_bw_engine (rtl/microbench/l3_memtest/l3_bw_engine.sv) ----
BW_REG = {
    "CTRL": 0x00,
    "BASE_ADDR": 0x04,
    "BURST_WORDS": 0x08,
    "BURST_COUNT": 0x0C,
    "STATUS": 0x10,
    "CYCLES_LO": 0x14,
    "CYCLES_HI": 0x18,
    "BURSTS_DONE": 0x1C,
}
BW_CTRL_START = 0
BW_CTRL_DIR_READ = 1
BW_ST_BUSY = 0
BW_ST_DONE = 1


class PollTimeout(TimeoutError):
    """A device never reached DONE within the allotted number of polls."""


def _poll_done(read_status, done_bit: int, *, poll_interval: float, max_polls: int) -> None:
    import time

    for _ in range(max_polls):
        if read_status() & (1 << done_bit):
            return
        if poll_interval:
            time.sleep(poll_interval)
    raise PollTimeout(f"device did not reach DONE within {max_polls} polls")


class HbTrainer:
    """Host-side control of one hb_trainer instance."""

    def __init__(self, transport, base: int = 0):
        self.t = transport
        self.base = base

    def _reg(self, name: str) -> int:
        return self.base + TRAINER_REG[name]

    def start(self) -> None:
        self.t.write32(self._reg("CTRL"), 1 << T_CTRL_START)

    def status(self) -> int:
        return self.t.read32(self._reg("STATUS"))

    def is_busy(self) -> bool:
        return bool(self.status() & (1 << T_ST_BUSY))

    def is_done(self) -> bool:
        return bool(self.status() & (1 << T_ST_DONE))

    def window_valid(self) -> bool:
        return bool(self.status() & (1 << T_ST_WINDOW_VALID))

    def wait_done(self, *, poll_interval: float = 0.01, max_polls: int = 100_000) -> None:
        _poll_done(self.status, T_ST_DONE, poll_interval=poll_interval, max_polls=max_polls)

    def window(self) -> dict:
        """Read back the trained window. Only meaningful once is_done() is true."""
        return {
            "lo": self.t.read32(self._reg("WIN_LO")),
            "hi": self.t.read32(self._reg("WIN_HI")),
            "width": self.t.read32(self._reg("WIN_WIDTH")),
            "center": self.t.read32(self._reg("WIN_CENTER")),
            "valid": self.window_valid(),
        }

    def train(self, *, poll_interval: float = 0.01, max_polls: int = 100_000) -> dict:
        """Run one full sweep and return the resulting window (start -> wait -> read back)."""
        self.start()
        self.wait_done(poll_interval=poll_interval, max_polls=max_polls)
        return self.window()


class L3Memtest:
    """Host-side control of one l3_memtest_engine instance."""

    def __init__(self, transport, base: int = 0):
        self.t = transport
        self.base = base

    def _reg(self, name: str) -> int:
        return self.base + MEMTEST_REG[name]

    def configure(self, *, seed: int, base_addr: int, span_words: int, pass_target: int) -> None:
        self.t.write32(self._reg("SEED"), seed & 0xFFFF)
        self.t.write32(self._reg("BASE_ADDR"), base_addr & 0x7FFFFF)
        self.t.write32(self._reg("SPAN_WORDS"), span_words)
        self.t.write32(self._reg("PASS_TARGET"), pass_target)

    def start(self) -> None:
        self.t.write32(self._reg("CTRL"), 1 << MT_CTRL_START)

    def status(self) -> int:
        return self.t.read32(self._reg("STATUS"))

    def is_done(self) -> bool:
        return bool(self.status() & (1 << MT_ST_DONE))

    def wait_done(self, *, poll_interval: float = 0.01, max_polls: int = 1_000_000) -> None:
        _poll_done(self.status, MT_ST_DONE, poll_interval=poll_interval, max_polls=max_polls)

    def results(self) -> dict:
        return {
            "pass_done": self.t.read32(self._reg("PASS_DONE")),
            "err_count": self.t.read32(self._reg("ERR_COUNT")),
            "err_addr": self.t.read32(self._reg("ERR_ADDR")),
        }

    def run(self, *, seed: int, base_addr: int, span_words: int, pass_target: int,
            poll_interval: float = 0.01, max_polls: int = 1_000_000) -> dict:
        self.configure(seed=seed, base_addr=base_addr, span_words=span_words,
                       pass_target=pass_target)
        self.start()
        self.wait_done(poll_interval=poll_interval, max_polls=max_polls)
        out = self.results()
        total_words = span_words * out["pass_done"]
        out["error_rate"] = (out["err_count"] / total_words) if total_words else float("nan")
        return out


class L3BwEngine:
    """Host-side control of one l3_bw_engine instance."""

    def __init__(self, transport, base: int = 0):
        self.t = transport
        self.base = base

    def _reg(self, name: str) -> int:
        return self.base + BW_REG[name]

    def configure(self, *, base_addr: int, burst_words: int, burst_count: int) -> None:
        self.t.write32(self._reg("BASE_ADDR"), base_addr & 0x7FFFFF)
        self.t.write32(self._reg("BURST_WORDS"), burst_words)
        self.t.write32(self._reg("BURST_COUNT"), burst_count)

    def start(self, *, dir_read: bool) -> None:
        ctrl = 1 << BW_CTRL_START
        if dir_read:
            ctrl |= 1 << BW_CTRL_DIR_READ
        self.t.write32(self._reg("CTRL"), ctrl)

    def status(self) -> int:
        return self.t.read32(self._reg("STATUS"))

    def is_done(self) -> bool:
        return bool(self.status() & (1 << BW_ST_DONE))

    def wait_done(self, *, poll_interval: float = 0.01, max_polls: int = 1_000_000) -> None:
        _poll_done(self.status, BW_ST_DONE, poll_interval=poll_interval, max_polls=max_polls)

    def results(self) -> dict:
        lo = self.t.read32(self._reg("CYCLES_LO"))
        hi = self.t.read32(self._reg("CYCLES_HI"))
        return {
            "bursts_done": self.t.read32(self._reg("BURSTS_DONE")),
            "cycles": (hi << 32) | lo,
        }

    def run(self, *, base_addr: int, burst_words: int, burst_count: int, dir_read: bool,
            fclk_mhz: float, poll_interval: float = 0.01, max_polls: int = 1_000_000) -> dict:
        """Run one direction's sweep and return cycles + derived MB/s (content is never checked;
        this engine only times arrival, see l3_bw_engine.sv)."""
        self.configure(base_addr=base_addr, burst_words=burst_words, burst_count=burst_count)
        self.start(dir_read=dir_read)
        self.wait_done(poll_interval=poll_interval, max_polls=max_polls)
        out = self.results()
        total_bytes = burst_words * burst_count * 2
        seconds = out["cycles"] / (fclk_mhz * 1e6) if fclk_mhz > 0 else float("nan")
        out["sustained_mbps"] = (total_bytes / seconds / 1e6) if seconds else float("nan")
        return out


class MockL3Transport(Transport):
    """In-memory model of hb_trainer + l3_memtest_engine + l3_bw_engine for tests (no board).

    Each device gets a distinct base address (mirroring how a real system maps three separate CSR
    blocks into one flat Avalon address space, docs/hyperbus.md #14 addendum). A test programs each
    device's canned outcome with `program_train`/`program_memtest`/`program_bw`; writing CTRL.START
    makes that device immediately report DONE with the programmed outcome — these are one-shot
    train/memtest/bandwidth runs (not a streaming counter), so unlike sw/host/transport.py's
    scoreboard MockTransport there is no ramp to model.
    """

    TRAINER_BASE = 0x0000
    MEMTEST_BASE = 0x1000
    BW_BASE = 0x2000

    def __init__(self):
        self.reg: dict[int, int] = {}
        self._train_result = {"lo": 0, "hi": 0, "width": 0, "center": 0, "valid": False}
        self._memtest_result = {"pass_done": 0, "err_count": 0, "err_addr": 0}
        self._bw_result = {"bursts_done": 0, "cycles": 0}
        self._train_done = False
        self._memtest_done = False
        self._bw_done = False

    # ---- test hooks ----
    def program_train(self, *, lo: int, hi: int, width: int, center: int, valid: bool) -> None:
        self._train_result = {"lo": lo, "hi": hi, "width": width, "center": center, "valid": valid}

    def program_memtest(self, *, pass_done: int, err_count: int, err_addr: int) -> None:
        self._memtest_result = {"pass_done": pass_done, "err_count": err_count,
                                 "err_addr": err_addr}

    def program_bw(self, *, bursts_done: int, cycles: int) -> None:
        self._bw_result = {"bursts_done": bursts_done, "cycles": cycles}

    # ---- Transport ----
    def read32(self, offset: int) -> int:
        if self.TRAINER_BASE <= offset < self.MEMTEST_BASE:
            off = offset - self.TRAINER_BASE
            if off == TRAINER_REG["STATUS"]:
                st = (1 << T_ST_DONE) if self._train_done else 0
                if self._train_done and self._train_result["valid"]:
                    st |= 1 << T_ST_WINDOW_VALID
                return st
            if off == TRAINER_REG["WIN_LO"]: return self._train_result["lo"]
            if off == TRAINER_REG["WIN_HI"]: return self._train_result["hi"]
            if off == TRAINER_REG["WIN_WIDTH"]: return self._train_result["width"]
            if off == TRAINER_REG["WIN_CENTER"]: return self._train_result["center"]
            return self.reg.get(offset, 0)
        if self.MEMTEST_BASE <= offset < self.BW_BASE:
            off = offset - self.MEMTEST_BASE
            if off == MEMTEST_REG["STATUS"]:
                return (1 << MT_ST_DONE) if self._memtest_done else 0
            if off == MEMTEST_REG["PASS_DONE"]: return self._memtest_result["pass_done"]
            if off == MEMTEST_REG["ERR_COUNT"]: return self._memtest_result["err_count"]
            if off == MEMTEST_REG["ERR_ADDR"]: return self._memtest_result["err_addr"]
            return self.reg.get(offset, 0)
        off = offset - self.BW_BASE
        if off == BW_REG["STATUS"]:
            return (1 << BW_ST_DONE) if self._bw_done else 0
        if off == BW_REG["BURSTS_DONE"]: return self._bw_result["bursts_done"]
        if off == BW_REG["CYCLES_LO"]: return self._bw_result["cycles"] & 0xFFFFFFFF
        if off == BW_REG["CYCLES_HI"]: return (self._bw_result["cycles"] >> 32) & 0xFFFFFFFF
        return self.reg.get(offset, 0)

    def write32(self, offset: int, value: int) -> None:
        value &= 0xFFFFFFFF
        if self.TRAINER_BASE <= offset < self.MEMTEST_BASE:
            off = offset - self.TRAINER_BASE
            if off == TRAINER_REG["CTRL"] and (value & (1 << T_CTRL_START)):
                self._train_done = True  # single-shot mock: sweep "instantly" completes
            else:
                self.reg[offset] = value
            return
        if self.MEMTEST_BASE <= offset < self.BW_BASE:
            off = offset - self.MEMTEST_BASE
            if off == MEMTEST_REG["CTRL"] and (value & (1 << MT_CTRL_START)):
                self._memtest_done = True
            else:
                self.reg[offset] = value
            return
        off = offset - self.BW_BASE
        if off == BW_REG["CTRL"] and (value & (1 << BW_CTRL_START)):
            self._bw_done = True
        else:
            self.reg[offset] = value

    def write_block(self, addr: int, data: bytes) -> None:
        raise NotImplementedError("MockL3Transport has no HyperRAM block model (CSR-only mock)")

    def read_block(self, addr: int, nbytes: int) -> bytes:
        raise NotImplementedError("MockL3Transport has no HyperRAM block model (CSR-only mock)")
