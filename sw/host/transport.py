"""Hardware transport abstraction for the benchmark host (issue #17).

Everything the host does to the board goes through one `Transport`: 32-bit register access to the
scoreboard CSR slave, and block access to HyperRAM. Keeping this the single choke point makes the
runner, loader, and scoreboard wrapper fully unit-testable against `MockTransport` with no board
(PLAN §6/§9 PH4). JTAG is the control plane only — never the timed data path (PLAN §8 method E).
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod

import scoreboard as sb


class Transport(ABC):
    """Register + memory access to the board. Addresses: reg32 = scoreboard CSR offsets;
    block = HyperRAM byte addresses."""

    @abstractmethod
    def read32(self, offset: int) -> int:
        """Read a 32-bit scoreboard CSR at `offset`."""

    @abstractmethod
    def write32(self, offset: int, value: int) -> None:
        """Write a 32-bit scoreboard CSR at `offset`."""

    @abstractmethod
    def write_block(self, addr: int, data: bytes) -> None:
        """Write `data` to HyperRAM starting at byte `addr`."""

    @abstractmethod
    def read_block(self, addr: int, nbytes: int) -> bytes:
        """Read `nbytes` from HyperRAM starting at byte `addr`."""


class MockTransport(Transport):
    """In-memory model of the scoreboard + HyperRAM for tests.

    Models enough of docs/register_map.md for the full runner flow: config registers, the
    START/SOFT_RESET control bits, the atomic snapshot latched on a CYCLES_LO read, the windowed
    histogram, and the STATUS bits. A test programs the "run outcome" with `program_run(...)`; on
    START the model ramps DONE toward the target over successive CYCLES_LO reads so the poll loop is
    exercised, then returns the exact programmed counters once complete.
    """

    def __init__(self, mem_size: int = 16 * 1024 * 1024):
        self.mem = bytearray(mem_size)
        self.reg: dict[int, int] = {off: 0 for off in sb.REG.values()}
        self.loop_en = False
        # programmed run outcome
        self._done_target = 0
        self._passes = 0
        self._cycles = 0
        self._lat_min = 0
        self._lat_max = 0
        self._hist = [0] * sb.HIST_ENTRIES
        self._ramp_step = 0
        # live/snapshot run state
        self._done_live = 0
        self._running = False
        self._snap = {"cycles": 0, "done": 0, "pass": 0, "lat_min": 0, "lat_max": 0}

    # ---- test hook ----
    def program_run(self, *, done: int, passes: int, cycles: int, lat_min: int, lat_max: int,
                    hist: list[int], ramp_step: int | None = None) -> None:
        self._done_target = done
        self._passes = passes
        self._cycles = cycles
        self._lat_min = lat_min
        self._lat_max = lat_max
        self._hist = list(hist) + [0] * (sb.HIST_ENTRIES - len(hist))
        self._ramp_step = ramp_step if ramp_step is not None else max(1, done)

    # ---- Transport ----
    def read32(self, offset: int) -> int:
        if offset == sb.REG["CYCLES_LO"]:
            self._take_snapshot()
            return self._snap["cycles"] & 0xFFFFFFFF
        if offset == sb.REG["CYCLES_HI"]:
            return (self._snap["cycles"] >> 32) & 0xFFFFFFFF
        if offset == sb.REG["DONE_COUNT"]:
            return self._snap["done"]
        if offset == sb.REG["PASS_COUNT"]:
            return self._snap["pass"]
        if offset == sb.REG["LAT_MIN"]:
            return self._snap["lat_min"]
        if offset == sb.REG["LAT_MAX"]:
            return self._snap["lat_max"]
        if offset == sb.REG["STATUS"]:
            return self._status()
        if offset == sb.REG["HIST_DATA"]:
            return self._hist[self.reg[sb.REG["HIST_ADDR"]] % sb.HIST_ENTRIES]
        return self.reg.get(offset, 0)

    def write32(self, offset: int, value: int) -> None:
        value &= 0xFFFFFFFF
        if offset == sb.REG["CTRL"]:
            self.loop_en = bool(value & (1 << sb.CTRL_LOOP_EN))
            if value & (1 << sb.CTRL_START):
                self._start_run()
            elif value & (1 << sb.CTRL_SOFT_RESET):
                self._clear()
        elif offset == sb.REG["HIST_ADDR"]:
            self.reg[offset] = value % sb.HIST_ENTRIES
        elif offset in self.reg:
            self.reg[offset] = value

    def write_block(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr + len(data)] = data

    def read_block(self, addr: int, nbytes: int) -> bytes:
        return bytes(self.mem[addr:addr + nbytes])

    # ---- internal scoreboard model ----
    def _start_run(self):
        self._running = True
        self._done_live = 0
        self._snap = {"cycles": 0, "done": 0, "pass": 0, "lat_min": 0, "lat_max": 0}

    def _clear(self):
        self._running = False
        self._done_live = 0
        self._snap = {"cycles": 0, "done": 0, "pass": 0, "lat_min": 0, "lat_max": 0}

    def _take_snapshot(self):
        if self._running:
            self._done_live = min(self._done_target, self._done_live + self._ramp_step)
            if self._done_live >= self._done_target:
                self._running = self.loop_en and False  # non-loop: done; loop: host stops us anyway
                self._snap = {"cycles": self._cycles, "done": self._done_target,
                              "pass": self._passes, "lat_min": self._lat_min, "lat_max": self._lat_max}
            else:
                # partial (values only need to keep the poll loop going)
                frac = self._done_live / max(1, self._done_target)
                self._snap = {"cycles": int(self._cycles * frac), "done": self._done_live,
                              "pass": int(self._passes * frac), "lat_min": self._lat_min,
                              "lat_max": self._lat_max}

    def _status(self) -> int:
        st = 0
        done_flag = (not self.loop_en) and self._done_target > 0 and self._done_live >= self._done_target
        if self._running and not done_flag:
            st |= 1 << sb.ST_RUNNING
        if done_flag:
            st |= 1 << sb.ST_DONE
        return st


class SystemConsoleTransport(Transport):
    """Drives Intel/Altera **System Console** over JTAG (control plane only).

    Not exercised in CI — requires the board + a `system-console` install. It opens a master service
    on the JTAG-to-Avalon master and issues `master_read_32` / `master_write_32` / block variants.
    Two masters are used: one addressing the scoreboard CSR slave (reg32), one addressing HyperRAM
    (block). Concrete wiring is filled in during board bring-up (#18); this class documents the
    contract and raises until then so nothing silently no-ops on hardware.
    """

    def __init__(self, csr_master: str, mem_master: str):
        # `system-console`'s Tcl/Python API handles the actual claim_service/master_* calls.
        self.csr_master = csr_master
        self.mem_master = mem_master
        raise NotImplementedError(
            "SystemConsoleTransport is wired up during board bring-up (#18); "
            "use MockTransport off-board")

    def read32(self, offset: int) -> int: ...          # pragma: no cover
    def write32(self, offset: int, value: int) -> None: ...   # pragma: no cover
    def write_block(self, addr: int, data: bytes) -> None: ...  # pragma: no cover
    def read_block(self, addr: int, nbytes: int) -> bytes: ...  # pragma: no cover


def sha256_window(transport: Transport, addr: int, nbytes: int) -> str:
    return hashlib.sha256(transport.read_block(addr, nbytes)).hexdigest()
