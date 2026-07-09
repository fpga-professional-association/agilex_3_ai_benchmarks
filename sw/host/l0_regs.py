"""Typed wrapper over the L0 tensor-chain microbench CSR map (issue #9).

The ONE place raw register offsets live on the host side for this module — mirrors
rtl/common/bench_pkg.sv's L0_ADDR_* / rtl/microbench/l0_tensor_chain/README.md register-map table
(a test enforces they stay in sync, same idea as sw/host/scoreboard.py + docs/register_map.md).
"""

from __future__ import annotations

REG = {
    "CTRL":       0x00,
    "N_VECTORS":  0x04,
    "CYCLES_LO":  0x08,
    "CYCLES_HI":  0x0C,
    "DONE_COUNT": 0x10,
    "CHECKSUM":   0x14,
    "STATUS":     0x18,
    "N_BLOCKS":   0x1C,
}

# CTRL bits
CTRL_START = 0

# STATUS bits
ST_RUNNING = 0
ST_DONE = 1


class Counters:
    """One coherent snapshot of the L0 counters (all from a single CYCLES_LO latch)."""

    __slots__ = ("cycles", "done", "checksum")

    def __init__(self, cycles: int, done: int, checksum: int):
        self.cycles = cycles
        self.done = done
        self.checksum = checksum

    def __repr__(self):
        return f"Counters(cycles={self.cycles}, done={self.done}, checksum=0x{self.checksum:08X})"


class L0TensorChain:
    """Typed access to l0_tensor_chain over a Transport (sw/host/transport.py)."""

    def __init__(self, transport):
        self.t = transport

    def n_blocks(self) -> int:
        """Read the compile-time N_BLOCKS CSR — cross-check against the .sof the caller intended
        to load before trusting any other number from this run."""
        return self.t.read32(REG["N_BLOCKS"])

    def configure(self, n_vectors: int) -> None:
        self.t.write32(REG["N_VECTORS"], n_vectors)

    def start(self) -> None:
        self.t.write32(REG["CTRL"], 1 << CTRL_START)

    def status(self) -> int:
        return self.t.read32(REG["STATUS"])

    def is_running(self) -> bool:
        return bool(self.status() & (1 << ST_RUNNING))

    def is_done(self) -> bool:
        return bool(self.status() & (1 << ST_DONE))

    def snapshot(self) -> Counters:
        """Latch and read one atomic counter snapshot (reading CYCLES_LO latches it)."""
        lo = self.t.read32(REG["CYCLES_LO"])          # latches the snapshot
        hi = self.t.read32(REG["CYCLES_HI"])
        done = self.t.read32(REG["DONE_COUNT"])
        checksum = self.t.read32(REG["CHECKSUM"])
        return Counters((hi << 32) | lo, done, checksum)
