"""Unit tests for sw/host/streaming_driver.py -- the DDR-free/streaming driver.

These pin down the RESOLVED parts of the protocol (register write/poll ordering, the hw_timer
bracket, the descriptor-full guard) against a mock port with made-up-but-consistent addresses
standing in for `StreamingRegisters`' four not-yet-platform-resolved fields (see that module's
docstring -- this repo's own DDR-free Qsys address map isn't built yet). No board, no Docker.
"""

import pytest

import coredla_csr_handshake as h
from streaming_driver import (
    SGDMA_CONTROL_START,
    SGDMA_CONTROL_STOP,
    SGDMA_STATUS_FULL_BIT,
    StreamingDriverError,
    StreamingInferenceDriver,
    StreamingRegisters,
)

INGRESS_CSR = 0x1000
EGRESS_CSR = 0x1004
INGRESS_MEM = 0x2000
EGRESS_MEM = 0x3000


def _regs():
    return StreamingRegisters(
        ingress_sgdma_csr_addr=INGRESS_CSR,
        egress_sgdma_csr_addr=EGRESS_CSR,
        ingress_onchip_mem_addr=INGRESS_MEM,
        egress_onchip_mem_addr=EGRESS_MEM,
    )


class MockStreamingPort:
    def __init__(self, *, complete_after_reads: int = 2, cycles: int = 555,
                 desc_full_addr: int | None = None):
        self.mem = bytearray(1 << 16)
        self.csr: dict[int, int] = {h.DLA_DMA_CSR_OFFSET_COMPLETION_COUNT: 0}
        self.csr_writes: list[tuple[int, int]] = []
        self._triggered = False
        self._reads_after_trigger = 0
        self._complete_after_reads = complete_after_reads
        self._cycles = cycles
        self._desc_full_addr = desc_full_addr  # if set, that CSR reports "full" once

    def write_ddr(self, addr, data):
        self.mem[addr:addr + len(data)] = data

    def read_ddr(self, addr, nbytes):
        return bytes(self.mem[addr:addr + nbytes])

    def csr_write32(self, addr, value):
        value &= 0xFFFFFFFF
        self.csr_writes.append((addr, value))
        self.csr[addr] = value
        if addr == h.DLA_CSR_OFFSET_READY_STREAMING_IFACE and value == 1:
            self._triggered = True

    def csr_read32(self, addr):
        if addr == self._desc_full_addr:
            return SGDMA_STATUS_FULL_BIT
        if addr == h.DLA_DMA_CSR_OFFSET_COMPLETION_COUNT:
            if self._triggered:
                self._reads_after_trigger += 1
                if self._reads_after_trigger >= self._complete_after_reads:
                    self.csr[addr] = 1
            return self.csr.get(addr, 0)
        if addr == h.DLA_HW_TIMER_OFFSET:
            return self._cycles
        return self.csr.get(addr, 0)


# ---- StreamingRegisters.require_resolved() -----------------------------------------------------

def test_require_resolved_raises_when_unset():
    with pytest.raises(NotImplementedError, match="Qsys address map"):
        StreamingRegisters().require_resolved()


def test_require_resolved_passes_when_all_set():
    _regs().require_resolved()  # should not raise


def test_driver_construction_validates_registers():
    with pytest.raises(NotImplementedError):
        StreamingInferenceDriver(StreamingRegisters())


# ---- reset_sgdmas() -------------------------------------------------------------------------

def test_reset_sgdmas_stops_then_starts_both():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort()
    driver.reset_sgdmas(port)
    assert port.csr_writes == [
        (EGRESS_CSR, SGDMA_CONTROL_STOP),
        (INGRESS_CSR, SGDMA_CONTROL_STOP),
        (EGRESS_CSR, SGDMA_CONTROL_START),
        (INGRESS_CSR, SGDMA_CONTROL_START),
    ]


# ---- descriptor queueing + full guard ----------------------------------------------------------

def test_queue_descriptors_write_size_to_csr():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort()
    driver.queue_ingress_descriptor(port, 1536)
    driver.queue_egress_descriptor(port, 512)
    assert port.csr_writes == [(INGRESS_CSR, 1536), (EGRESS_CSR, 512)]


def test_queue_ingress_raises_when_full():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort(desc_full_addr=INGRESS_CSR)
    with pytest.raises(StreamingDriverError, match="ingress"):
        driver.queue_ingress_descriptor(port, 1536)


def test_queue_egress_raises_when_full():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort(desc_full_addr=EGRESS_CSR)
    with pytest.raises(StreamingDriverError, match="egress"):
        driver.queue_egress_descriptor(port, 512)


# ---- stage_input / read_output --------------------------------------------------------------

def test_stage_input_writes_to_ingress_mem():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort()
    driver.stage_input(port, b"\x01\x02\x03")
    assert bytes(port.mem[INGRESS_MEM:INGRESS_MEM + 3]) == b"\x01\x02\x03"


def test_read_output_reads_from_egress_mem():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort()
    port.mem[EGRESS_MEM:EGRESS_MEM + 4] = b"\xde\xad\xbe\xef"
    assert driver.read_output(port, 4) == b"\xde\xad\xbe\xef"


# ---- full run_one_inference(): ordering + hw_timer bracket + result -----------------------------

def test_run_one_inference_full_sequence_and_timer_bracket():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort(complete_after_reads=2, cycles=999)
    port.mem[EGRESS_MEM:EGRESS_MEM + 4] = b"\xaa\xbb\xcc\xdd"

    output, cycles = driver.run_one_inference(port, input_bytes=b"\x01\x02", output_bytes=4,
                                              timeout_s=5.0)

    assert output == b"\xaa\xbb\xcc\xdd"
    assert cycles == 999
    # input landed in ingress mem before the trigger
    assert bytes(port.mem[INGRESS_MEM:INGRESS_MEM + 2]) == b"\x01\x02"
    # descriptors queued, then hw_timer start, then trigger, then stop -- in that order
    addrs = [a for a, _ in port.csr_writes]
    assert addrs.index(INGRESS_CSR) < addrs.index(h.DLA_HW_TIMER_OFFSET)
    start_idx = port.csr_writes.index((h.DLA_HW_TIMER_OFFSET, h.DLA_HW_TIMER_START))
    trigger_idx = port.csr_writes.index((h.DLA_CSR_OFFSET_READY_STREAMING_IFACE, 1))
    stop_idx = port.csr_writes.index((h.DLA_HW_TIMER_OFFSET, h.DLA_HW_TIMER_STOP))
    assert start_idx < trigger_idx < stop_idx


def test_run_one_inference_propagates_timeout():
    driver = StreamingInferenceDriver(_regs())
    port = MockStreamingPort(complete_after_reads=10**9)
    with pytest.raises(h.InferenceError, match="did not complete"):
        driver.run_one_inference(port, input_bytes=b"\x01", output_bytes=1, timeout_s=0.05)
