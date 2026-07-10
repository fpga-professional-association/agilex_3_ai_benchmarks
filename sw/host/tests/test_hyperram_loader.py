"""Unit tests for sw/host/hyperram_loader.py -- guard-banded load + parity-gated inference.

Fully off-board: `MockHyperRamPort` models just enough of the CSR handshake (COMPLETION_COUNT,
DESC_DIAGNOSTICS, the hw_timer) and a flat HyperRAM byte array to exercise the real
`CoreDlaCsrHandshake` + `hyperram_loader` code paths -- no system-console, no board.
"""

import pytest

import coredla_csr_handshake as h
from aot_layout import parse_ddr_buffer_info, resolve_hyperram_layout
from hyperram_loader import (
    InferenceResult,
    ParityError,
    load_and_run,
    load_config_filter,
    load_input,
    run_one_inference,
    run_one_inference_with_parity,
)

RESNET8_TEXT = """
inputOutputBuffer size: 33280
\tInputs:
\t\tinput_1: offset 0, size: 32768
\tOutput: offset 32768, size: 512

configFilterBuffer size: 208896
\t Config: offset 0, size: 22528
\t Filter: offset 22528, size: 186368
\t Bias+Scale: offset 208896, size: 0

interBuffer size: 0
"""


def _layout():
    return resolve_hyperram_layout(parse_ddr_buffer_info(RESNET8_TEXT))


class MockHyperRamPort:
    """Flat byte-addressed HyperRAM + a CSR model that, on the trigger write, deposits a canned
    output tensor at the layout's output address after `complete_after_reads` polls -- enough to
    drive `CoreDlaCsrHandshake.run_inference_timed` and `hyperram_loader`'s read-back for real."""

    def __init__(self, layout, *, canned_output: bytes, cycles: int = 4242,
                 complete_after_reads: int = 2, mem_size: int = 1 << 20):
        self.layout = layout
        self.mem = bytearray(mem_size)
        self.canned_output = canned_output
        self.cycles = cycles
        self.reg: dict[int, int] = {h.DLA_DMA_CSR_OFFSET_COMPLETION_COUNT: 0,
                                    h.DLA_DMA_CSR_OFFSET_DESC_DIAGNOSTICS: 0}
        self._triggered = False
        self._reads_after_trigger = 0
        self._complete_after_reads = complete_after_reads
        self.write_ddr_calls: list[tuple[int, int]] = []   # (addr, len) per call, in order
        self.csr_writes: list[tuple[int, int]] = []

    # ---- DdrPort ----
    def write_ddr(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr + len(data)] = data
        self.write_ddr_calls.append((addr, len(data)))

    def read_ddr(self, addr: int, nbytes: int) -> bytes:
        return bytes(self.mem[addr:addr + nbytes])

    # ---- CsrPort ----
    def csr_write32(self, addr: int, value: int) -> None:
        value &= 0xFFFFFFFF
        self.csr_writes.append((addr, value))
        self.reg[addr] = value
        if addr == h.DLA_DMA_CSR_OFFSET_INPUT_OUTPUT_BASE_ADDR:
            self._triggered = True

    def csr_read32(self, addr: int) -> int:
        if addr == h.DLA_DMA_CSR_OFFSET_COMPLETION_COUNT:
            if self._triggered:
                self._reads_after_trigger += 1
                if self._reads_after_trigger >= self._complete_after_reads:
                    self.reg[addr] = 1
                    # "hardware" deposits the output once done
                    self.mem[self.layout.output_addr:self.layout.output_addr + len(self.canned_output)] = \
                        self.canned_output
            return self.reg.get(addr, 0)
        if addr == h.DLA_HW_TIMER_OFFSET:
            return self.cycles
        return self.reg.get(addr, 0)


def _blobs(layout):
    config_filter = bytes((i % 251) for i in range(layout.config_filter_write_bytes))
    input_bytes = bytes((i % 199) for i in range(layout.input_bytes))
    return config_filter, input_bytes


# ---- load_config_filter / load_input: sizes + addresses ----------------------------------------

def test_load_config_filter_writes_at_config_base_addr():
    layout = _layout()
    port = MockHyperRamPort(layout, canned_output=b"\x00" * layout.output_bytes)
    config_filter, _ = _blobs(layout)
    load_config_filter(port, layout, config_filter)
    assert port.write_ddr_calls == [(layout.config_base_addr, len(config_filter))]
    assert bytes(port.mem[layout.config_base_addr:layout.config_base_addr + len(config_filter)]) == config_filter


def test_load_config_filter_rejects_wrong_size():
    layout = _layout()
    port = MockHyperRamPort(layout, canned_output=b"\x00" * layout.output_bytes)
    with pytest.raises(ValueError, match="config\\+filter"):
        load_config_filter(port, layout, b"\x00" * 4)


def test_load_input_writes_at_input_addr():
    layout = _layout()
    port = MockHyperRamPort(layout, canned_output=b"\x00" * layout.output_bytes)
    _, input_bytes = _blobs(layout)
    load_input(port, layout, input_bytes)
    assert port.write_ddr_calls == [(layout.input_addr, len(input_bytes))]


def test_load_input_rejects_wrong_size():
    layout = _layout()
    port = MockHyperRamPort(layout, canned_output=b"\x00" * layout.output_bytes)
    with pytest.raises(ValueError, match="input tensor"):
        load_input(port, layout, b"\x00" * 4)


# ---- run_one_inference: handshake + hw_timer + read-back ---------------------------------------

def test_run_one_inference_returns_output_cycles_and_completion():
    layout = _layout()
    canned = bytes((i % 256) for i in range(layout.output_bytes))
    port = MockHyperRamPort(layout, canned_output=canned, cycles=777)
    result = run_one_inference(port, layout, timeout_s=5.0)
    assert isinstance(result, InferenceResult)
    assert result.output == canned
    assert result.cycles == 777
    assert result.completion_count == 1


def test_run_one_inference_reads_exactly_output_region():
    layout = _layout()
    canned = bytes((i % 256) for i in range(layout.output_bytes))
    port = MockHyperRamPort(layout, canned_output=canned)
    result = run_one_inference(port, layout, timeout_s=5.0)
    assert len(result.output) == layout.output_bytes


# ---- parity gating -------------------------------------------------------------------------------

def test_parity_passes_on_match():
    layout = _layout()
    canned = bytes((i % 256) for i in range(layout.output_bytes))
    port = MockHyperRamPort(layout, canned_output=canned)
    result = run_one_inference_with_parity(port, layout, reference_output=canned, timeout_s=5.0)
    assert result.output == canned


def test_parity_raises_on_mismatch():
    layout = _layout()
    canned = bytes((i % 256) for i in range(layout.output_bytes))
    wrong_reference = bytes((i + 1) % 256 for i in range(layout.output_bytes))
    port = MockHyperRamPort(layout, canned_output=canned)
    with pytest.raises(ParityError, match="does not match"):
        run_one_inference_with_parity(port, layout, reference_output=wrong_reference, timeout_s=5.0)


# ---- load_and_run: full end-to-end sequence -----------------------------------------------------

def test_load_and_run_full_sequence_with_parity():
    layout = _layout()
    config_filter, input_bytes = _blobs(layout)
    canned = bytes((i % 256) for i in range(layout.output_bytes))
    port = MockHyperRamPort(layout, canned_output=canned)

    result = load_and_run(port, layout, config_filter_bytes=config_filter, input_bytes=input_bytes,
                          reference_output=canned, timeout_s=5.0)

    assert result.output == canned
    # config+filter and input were both written, config first (order doesn't strictly matter to the
    # hardware, but this pins down the sequence the driver actually issues)
    assert port.write_ddr_calls == [
        (layout.config_base_addr, layout.config_filter_write_bytes),
        (layout.input_addr, layout.input_bytes),
    ]


def test_load_and_run_without_reference_skips_parity():
    layout = _layout()
    config_filter, input_bytes = _blobs(layout)
    canned = bytes((i % 256) for i in range(layout.output_bytes))
    port = MockHyperRamPort(layout, canned_output=canned)
    result = load_and_run(port, layout, config_filter_bytes=config_filter, input_bytes=input_bytes)
    assert result.output == canned


def test_load_and_run_refuses_on_parity_mismatch_before_reporting():
    layout = _layout()
    config_filter, input_bytes = _blobs(layout)
    canned = bytes((i % 256) for i in range(layout.output_bytes))
    wrong_reference = b"\xff" * layout.output_bytes
    port = MockHyperRamPort(layout, canned_output=canned)
    with pytest.raises(ParityError):
        load_and_run(port, layout, config_filter_bytes=config_filter, input_bytes=input_bytes,
                    reference_output=wrong_reference)
