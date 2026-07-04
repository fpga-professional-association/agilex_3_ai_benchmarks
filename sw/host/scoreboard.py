"""Typed wrapper over the scoreboard CSR map (issue #17).

The ONE place raw register offsets live on the host side — everything else calls these methods
(AGENTS.md: no register addresses scattered across files). Offsets mirror docs/register_map.md and
a test parses that doc to prove they stay in sync.
"""

from __future__ import annotations

# Byte offsets — must match docs/register_map.md exactly (test_scoreboard_regs.py enforces this).
REG = {
    "CTRL":       0x00,
    "N_RECORDS":  0x04,
    "REC_STRIDE": 0x08,
    "REC_BASE":   0x0C,
    "CYCLES_LO":  0x10,
    "CYCLES_HI":  0x14,
    "DONE_COUNT": 0x18,
    "PASS_COUNT": 0x1C,
    "LAT_MIN":    0x20,
    "LAT_MAX":    0x24,
    "STATUS":     0x28,
    "HIST_SHIFT": 0x2C,
    "HIST_ADDR":  0x30,
    "HIST_DATA":  0x34,
    "LOG_BASE":   0x38,
}

# CTRL bits
CTRL_START = 0
CTRL_LOOP_EN = 1
CTRL_SOFT_RESET = 2

# STATUS bits
ST_RUNNING = 0
ST_DONE = 1
ST_ISSUE_OVF = 2
ST_TS_UNDER = 3
ST_CLEARING = 4

HIST_ENTRIES = 64


class Counters:
    """One coherent snapshot of the scoreboard counters (all from a single CYCLES_LO latch)."""

    __slots__ = ("cycles", "done", "passes", "lat_min", "lat_max")

    def __init__(self, cycles: int, done: int, passes: int, lat_min: int, lat_max: int):
        self.cycles = cycles
        self.done = done
        self.passes = passes
        self.lat_min = lat_min
        self.lat_max = lat_max

    def __repr__(self):
        return (f"Counters(cycles={self.cycles}, done={self.done}, passes={self.passes}, "
                f"lat_min={self.lat_min}, lat_max={self.lat_max})")


class Scoreboard:
    """Typed access to the scoreboard over a Transport."""

    def __init__(self, transport):
        self.t = transport

    def configure(self, n_records: int, rec_stride: int, rec_base: int,
                  hist_shift: int, log_base: int = 0) -> None:
        self.t.write32(REG["N_RECORDS"], n_records)
        self.t.write32(REG["REC_STRIDE"], rec_stride)
        self.t.write32(REG["REC_BASE"], rec_base)
        self.t.write32(REG["HIST_SHIFT"], hist_shift)
        self.t.write32(REG["LOG_BASE"], log_base)

    def start(self, loop: bool = False) -> None:
        ctrl = 1 << CTRL_START
        if loop:
            ctrl |= 1 << CTRL_LOOP_EN
        self.t.write32(REG["CTRL"], ctrl)

    def soft_reset(self) -> None:
        self.t.write32(REG["CTRL"], 1 << CTRL_SOFT_RESET)

    def status(self) -> int:
        return self.t.read32(REG["STATUS"])

    def is_running(self) -> bool:
        return bool(self.status() & (1 << ST_RUNNING))

    def is_done(self) -> bool:
        return bool(self.status() & (1 << ST_DONE))

    def hist_shift(self) -> int:
        return self.t.read32(REG["HIST_SHIFT"]) & 0x3F

    def snapshot(self) -> Counters:
        """Latch and read one atomic counter snapshot (CYCLES_LO read latches; §6 snapshot rule)."""
        lo = self.t.read32(REG["CYCLES_LO"])          # latches the snapshot
        hi = self.t.read32(REG["CYCLES_HI"])
        done = self.t.read32(REG["DONE_COUNT"])
        passes = self.t.read32(REG["PASS_COUNT"])
        lat_min = self.t.read32(REG["LAT_MIN"])
        lat_max = self.t.read32(REG["LAT_MAX"])
        return Counters((hi << 32) | lo, done, passes, lat_min, lat_max)

    def read_histogram(self) -> list[int]:
        out = []
        for b in range(HIST_ENTRIES):
            self.t.write32(REG["HIST_ADDR"], b)
            out.append(self.t.read32(REG["HIST_DATA"]))
        return out
