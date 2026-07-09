"""Unit tests for the CoreDLA CSR start/done handshake (sw/host/coredla_csr_handshake.py).

Pure-logic tests: they drive the handshake over an in-memory mock CSR port, with NO board and NO
system-console. They pin down the exact register offsets, the exact write ORDER (trigger last),
and the completion-poll behaviour against the vendor-sourced contract in dla_dma_csr.sv."""

import pytest

import coredla_csr_handshake as h
from coredla_csr_handshake import (
    CoreDlaCsrHandshake,
    InferenceError,
    InferenceJob,
    DLA_DMA_CSR_OFFSET_COMPLETION_COUNT as COMPLETION,
    DLA_DMA_CSR_OFFSET_CONFIG_BASE_ADDR as CONFIG_BASE,
    DLA_DMA_CSR_OFFSET_CONFIG_RANGE_MINUS_TWO as CONFIG_RANGE,
    DLA_DMA_CSR_OFFSET_INPUT_OUTPUT_BASE_ADDR as IO_BASE,
    DLA_DMA_CSR_OFFSET_INTERMEDIATE_BASE_ADDR as INTERMEDIATE,
    DLA_DMA_CSR_OFFSET_DESC_DIAGNOSTICS as DESC_DIAG,
    DLA_DMA_CSR_OFFSET_INTERRUPT_CONTROL as IRQ_CTRL,
    DLA_DMA_CSR_OFFSET_INTERRUPT_MASK as IRQ_MASK,
    DLA_DMA_CSR_DESC_DIAGNOSTICS_OVERFLOW_BIT,
    DLA_DMA_CSR_DESC_DIAGNOSTICS_OUT_OF_INFERENCES_BIT,
)


class MockCsrPort:
    """In-memory CSR model that reproduces the two hardware behaviours the handshake depends on:

    - COMPLETION_COUNT is a read register that advances by `completion_step` on the Nth CSR read
      after the trigger write (mimicking i_token_done pulsing once the job finishes).
    - writing IO_BASE is the trigger (dla_dma_csr.sv enqueue_descriptor); we record when it fired.
    Every write is logged in order so tests can assert the exact sequence.
    """

    def __init__(self, *, complete_after_reads: int = 2, completion_step: int = 1,
                 initial_completion: int = 0, desc_diag: int = 0):
        self.reg: dict[int, int] = {COMPLETION: initial_completion, DESC_DIAG: desc_diag}
        self.writes: list[tuple[int, int]] = []
        self.reads: list[int] = []
        self._triggered = False
        self._completion_reads_after_trigger = 0
        self._complete_after_reads = complete_after_reads
        self._completion_step = completion_step

    def csr_write32(self, addr: int, value: int) -> None:
        self.writes.append((addr, value & 0xFFFFFFFF))
        self.reg[addr] = value & 0xFFFFFFFF
        if addr == IO_BASE:
            self._triggered = True

    def csr_read32(self, addr: int) -> int:
        self.reads.append(addr)
        if addr == COMPLETION and self._triggered:
            self._completion_reads_after_trigger += 1
            if self._completion_reads_after_trigger >= self._complete_after_reads:
                self.reg[COMPLETION] = (self.reg[COMPLETION] + self._completion_step) & 0xFFFFFFFF
        return self.reg.get(addr, 0)


def _job():
    # total_config_bytes = 80 -> 80/8 = 10 words -> range = 8
    return InferenceJob(config_base_addr=0x0010_0000, total_config_bytes=80,
                        input_addr=0x0000_2000, intermediate_addr=0x0)


# ---- start(): exact offsets, values, and order ------------------------------------------------

def test_start_writes_expected_offsets_and_values():
    port = MockCsrPort()
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(port, _job())

    assert baseline == 0
    assert port.writes == [
        (INTERMEDIATE, 0x0),
        (CONFIG_BASE, 0x0010_0000),
        (CONFIG_RANGE, 8),          # (80/8) - 2
        (IO_BASE, 0x0000_2000),
    ]


def test_trigger_write_is_last():
    """The I/O-base write MUST be the final write — it is the enqueue/GO in dla_dma_csr.sv."""
    port = MockCsrPort()
    CoreDlaCsrHandshake(poll_interval_s=0).start(port, _job())
    assert port.writes[-1][0] == IO_BASE


def test_baseline_read_is_completion_count_before_trigger():
    port = MockCsrPort(initial_completion=7)
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(port, _job())
    assert baseline == 7
    # the first CSR access is the baseline read, before any write
    assert port.reads[0] == COMPLETION


# ---- config range arithmetic (the "minus two" down-counter) -----------------------------------

def test_config_range_minus_two():
    assert InferenceJob(0, 80).config_range_minus_two() == 8         # 10 words - 2
    assert InferenceJob(0, 16).config_range_minus_two() == 0         # 2 words - 2


def test_config_range_rejects_non_multiple():
    with pytest.raises(ValueError):
        InferenceJob(0, 81).config_range_minus_two()


def test_config_range_rejects_too_small():
    with pytest.raises(ValueError):
        InferenceJob(0, 8).config_range_minus_two()                  # 1 word -> range -1


# ---- wait_for_done(): polling completion count ------------------------------------------------

def test_wait_for_done_returns_when_completion_advances():
    port = MockCsrPort(complete_after_reads=3)
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(port, _job())
    final = hs.wait_for_done(port, baseline, timeout_s=5.0)
    assert final == baseline + 1


def test_wait_for_done_wraps_around_32bit():
    port = MockCsrPort(complete_after_reads=1, initial_completion=0xFFFF_FFFF)
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(port, _job())
    assert baseline == 0xFFFF_FFFF
    final = hs.wait_for_done(port, baseline, timeout_s=5.0)
    assert final == 0            # wrapped, still counts as one job done


def test_wait_for_done_times_out_if_never_completes():
    port = MockCsrPort(complete_after_reads=10**9)  # effectively never
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(port, _job())
    with pytest.raises(InferenceError, match="did not complete"):
        hs.wait_for_done(port, baseline, timeout_s=0.05)


# ---- descriptor-queue health guards -----------------------------------------------------------

def test_overflow_diagnostic_raises():
    port = MockCsrPort(complete_after_reads=10**9,
                       desc_diag=1 << DLA_DMA_CSR_DESC_DIAGNOSTICS_OVERFLOW_BIT)
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(port, _job())
    with pytest.raises(InferenceError, match="overflow"):
        hs.wait_for_done(port, baseline, timeout_s=1.0)


def test_out_of_inferences_diagnostic_raises():
    port = MockCsrPort(complete_after_reads=10**9,
                       desc_diag=1 << DLA_DMA_CSR_DESC_DIAGNOSTICS_OUT_OF_INFERENCES_BIT)
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(port, _job())
    with pytest.raises(InferenceError, match="out-of-inferences"):
        hs.wait_for_done(port, baseline, timeout_s=1.0)


# ---- run_inference(): full sequence over a mock port ------------------------------------------

def test_run_inference_full_sequence():
    port = MockCsrPort(complete_after_reads=2)
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    final = hs.run_inference(port, _job(), timeout_s=5.0)
    assert final == 1
    # the four config/trigger writes happened, trigger last
    assert [w[0] for w in port.writes] == [INTERMEDIATE, CONFIG_BASE, CONFIG_RANGE, IO_BASE]


def test_run_inference_works_with_smoke_infer_mock_transport():
    """The handshake operates on any CsrPort — including smoke_infer's own MockTransport, proving
    the drop-in surface matches. MockTransport.run_inference is a no-op, so we drive the handshake
    directly against it and just check the CSR writes landed in its csr dict."""
    smoke = pytest.importorskip("smoke_infer")
    t = smoke.MockTransport()
    # MockTransport.csr_read32 returns 0 for unset regs, so completion never advances -> expect
    # timeout; we only assert the writes were issued in order before the timeout.
    hs = CoreDlaCsrHandshake(poll_interval_s=0)
    baseline = hs.start(t, _job())
    assert baseline == 0
    assert t.csr[CONFIG_BASE] == 0x0010_0000
    assert t.csr[CONFIG_RANGE] == 8
    assert t.csr[IO_BASE] == 0x0000_2000
    with pytest.raises(InferenceError):
        hs.wait_for_done(t, baseline, timeout_s=0.05)


# ---- configure_interrupts(): mirrors the device constructor -----------------------------------

def test_configure_interrupts_clears_then_unmasks():
    port = MockCsrPort()
    CoreDlaCsrHandshake(poll_interval_s=0).configure_interrupts(port)
    assert port.writes == [(IRQ_CTRL, h.ALL_INTERRUPTS_MASK), (IRQ_MASK, h.ALL_INTERRUPTS_MASK)]
