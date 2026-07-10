#!/usr/bin/env python3
"""DDR-free / streaming inference driver (Track DRV, step 4) -- PARTIALLY resolved, flagged honestly.

Weights are on-chip MIF ROMs for a DDR-free build (nothing to load into DDR/HyperRAM); input goes in
via `ingress_onchip_memory` + `ingress_msgdma`, output comes out via the egress side, gated by the
streaming-ready/completion CSRs instead of the DDR-backed `CONFIG_BASE_ADDR`/`INPUT_OUTPUT_BASE_ADDR`
registers `coredla_csr_handshake.py` resolves for the HyperRAM path.

WHAT IS RESOLVED (from vendor source, cited below):
  - `coredla_batch_job.cpp`'s `disableExternalMemory_ && enableIstream_` branch (see
    `coredla_csr_handshake.py`'s module docstring, "DDR-free / streaming variant") SKIPS the four
    DDR-backed CSR writes entirely and instead writes `READY_STREAMING_IFACE`
    (`DLA_CSR_OFFSET_READY_STREAMING_IFACE`, offset 0x22c -- already in `coredla_csr_handshake.py`'s
    register map) and calls the MMD's `StreamInData`/`StreamOutData`.
  - The FPGA AI Suite ships a COMPLETE worked example of exactly this over System Console:
    `$COREDLA_ROOT/runtime/streaming/ed0_streaming_example/` (README + `system_console_script.tcl` +
    `include/system_console_lib.tcl`). Its `main_functional` proc's shape (assert_reset ->
    initialize_coredla -> stage_input (`master_write_from_file` into ingress on-chip memory) ->
    queue_ingress_descriptor / queue_egress_descriptor (write the transfer size to each mSGDMA's own
    descriptor-queue CSR window) -> poll completion (`check_inference_count`, `poll_register`) ->
    read_output (`master_read_to_file` from egress on-chip memory)) is the real, vendor-authored
    protocol shape this module mirrors as `StreamingRegisters`/`run_one_inference`.
  - The hw_timer-based latency measurement (`get_performance` in the same lib) uses the SAME
    start/stop/read-count register this repo's `coredla_csr_handshake.DLA_HW_TIMER_OFFSET` (0x800)
    already resolves for the DDR-backed path -- reused here unchanged.

WHAT IS **NOT** RESOLVED YET (flagging per AGENTS.md, not fabricating):
  `ed0_streaming_example` targets the Agilex 7 I-series dev kit's OWN Qsys system -- its
  `EGRESS_SGDMA_CSR_ADDR` / `INGRESS_SGDMA_CSR_ADDR` / ingress+egress on-chip-memory base addresses
  (defined near the top of `include/system_console_lib.tcl`) are specific to THAT platform's address
  map, not this repo's AXC3000/AGX3 DDR-free build (`quartus/coredla_agx3_ddrfree/`). This repo's own
  DDR-free platform has not yet been carried through `qsys-generate` to a build/ tree with a resolved
  Qsys address map (only the architecture files + the build script exist as of this writing --
  `quartus/coredla_agx3_ddrfree/arch/`, `scripts/build.sh`). Until that build exists, the four
  addresses below are placeholders the caller MUST supply (there is nothing to default them to
  without inventing a number). Resolving them is a config-time reading of THIS platform's own
  `<system>.qsys`/`ed_zero.tcl`-generated address map (the same class of exercise as
  `docs/coredla_hyperram_onboard_findings.md`'s CSR base-address discovery for the HyperRAM path),
  not a new unknown protocol.
"""

from __future__ import annotations

from dataclasses import dataclass

from coredla_csr_handshake import (
    CoreDlaCsrHandshake,
    CsrPort,
    DLA_CSR_OFFSET_READY_STREAMING_IFACE,
    DLA_DMA_CSR_OFFSET_COMPLETION_COUNT,
    _MASK32,
)


@dataclass
class StreamingRegisters:
    """The DDR-free/streaming CSR addresses this repo's OWN ddrfree platform needs, resolved from
    ITS Qsys address map (see module docstring "NOT RESOLVED YET"). All byte offsets, added to
    whatever base the transport's csr_write32/csr_read32 already applies (mirrors
    `coredla_csr_handshake`'s convention: callers pass byte offsets, not absolute addresses).
    """

    ready_streaming_iface: int = DLA_CSR_OFFSET_READY_STREAMING_IFACE
    ingress_sgdma_csr_addr: int | None = None    # ed0 lib: INGRESS_SGDMA_CSR_ADDR -- TBD this platform
    egress_sgdma_csr_addr: int | None = None     # ed0 lib: EGRESS_SGDMA_CSR_ADDR -- TBD this platform
    ingress_onchip_mem_addr: int | None = None   # ed0 lib: stage_input's target -- TBD this platform
    egress_onchip_mem_addr: int | None = None    # ed0 lib: read_output's source -- TBD this platform

    def require_resolved(self) -> None:
        missing = [name for name in (
            "ingress_sgdma_csr_addr", "egress_sgdma_csr_addr",
            "ingress_onchip_mem_addr", "egress_onchip_mem_addr")
            if getattr(self, name) is None]
        if missing:
            raise NotImplementedError(
                f"StreamingRegisters is missing this platform's own address(es) for {missing} -- "
                "these come from quartus/coredla_agx3_ddrfree's OWN Qsys address map, not from the "
                "Agilex-7 ed0_streaming_example reference (see module docstring). Resolve them from "
                "a real qsys-generate build of that platform before running on-board.")


# mSGDMA descriptor-status bits (system_console_lib.tcl `check_descriptor_buffer_full`): bit2 =
# descriptor queue full.
SGDMA_STATUS_FULL_BIT = 1 << 2
# Reset control values (system_console_lib.tcl `stop_sgdmas`/`start_sgdmas`): write 0x20 to stop,
# 0x0 to (re)start.
SGDMA_CONTROL_STOP = 0x20
SGDMA_CONTROL_START = 0x0


class StreamingDriverError(RuntimeError):
    pass


class StreamingInferenceDriver:
    """DDR-free single-inference driver: push input via ingress on-chip memory + msgDMA, trigger,
    poll completion, pull output via egress on-chip memory + msgDMA. Weights are on-chip MIF ROMs --
    nothing to load. Mirrors `ed0_streaming_example`'s `main_functional` shape; see module docstring
    for exactly which pieces are resolved vs. still platform-specific.

    `port` must expose `csr_write32`/`csr_read32` (register pokes) and `write_ddr`/`read_ddr` (block
    transfers to/from the ingress/egress on-chip memories -- same shape as the HyperRAM `DdrPort`,
    just a different address range).
    """

    def __init__(self, regs: StreamingRegisters, *, handshake: CoreDlaCsrHandshake | None = None):
        regs.require_resolved()
        self.regs = regs
        self.handshake = handshake or CoreDlaCsrHandshake()

    def reset_sgdmas(self, port: CsrPort) -> None:
        """`stop_sgdmas` then `start_sgdmas` (system_console_lib.tcl) -- clears any stale descriptor
        state before the first inference."""
        port.csr_write32(self.regs.egress_sgdma_csr_addr, SGDMA_CONTROL_STOP)
        port.csr_write32(self.regs.ingress_sgdma_csr_addr, SGDMA_CONTROL_STOP)
        port.csr_write32(self.regs.egress_sgdma_csr_addr, SGDMA_CONTROL_START)
        port.csr_write32(self.regs.ingress_sgdma_csr_addr, SGDMA_CONTROL_START)

    def stage_input(self, port, input_bytes: bytes) -> None:
        """Write the input tensor into ingress on-chip memory (`stage_input` /
        `master_write_from_file` in system_console_lib.tcl)."""
        port.write_ddr(self.regs.ingress_onchip_mem_addr, input_bytes)

    def queue_ingress_descriptor(self, port: CsrPort, size_bytes: int) -> None:
        self._check_not_full(port, self.regs.ingress_sgdma_csr_addr, "ingress")
        port.csr_write32(self.regs.ingress_sgdma_csr_addr, size_bytes & _MASK32)

    def queue_egress_descriptor(self, port: CsrPort, size_bytes: int) -> None:
        self._check_not_full(port, self.regs.egress_sgdma_csr_addr, "egress")
        port.csr_write32(self.regs.egress_sgdma_csr_addr, size_bytes & _MASK32)

    @staticmethod
    def _check_not_full(port: CsrPort, csr_addr: int, which: str) -> None:
        status = port.csr_read32(csr_addr)
        if status & SGDMA_STATUS_FULL_BIT:
            raise StreamingDriverError(f"{which} mSGDMA descriptor queue is full")

    def trigger(self, port: CsrPort) -> None:
        """Write READY_STREAMING_IFACE=1 -- the DDR-free path's equivalent of the DDR-backed
        INPUT_OUTPUT_BASE_ADDR GO trigger (`coredla_batch_job.cpp`'s istream branch)."""
        port.csr_write32(self.regs.ready_streaming_iface, 1)

    def read_output(self, port, output_bytes: int) -> bytes:
        return port.read_ddr(self.regs.egress_onchip_mem_addr, output_bytes)

    def run_one_inference(self, port, *, input_bytes: bytes, output_bytes: int,
                          timeout_s: float = 30.0) -> tuple[bytes, int]:
        """Full DDR-free single-inference sequence, hw_timer-bracketed. Returns (output, cycles)."""
        self.stage_input(port, input_bytes)
        self.queue_ingress_descriptor(port, len(input_bytes))
        self.queue_egress_descriptor(port, output_bytes)

        self.handshake.start_hw_timer(port)
        self.trigger(port)
        self._poll_completion(port, timeout_s=timeout_s)
        self.handshake.stop_hw_timer(port)
        cycles = self.handshake.read_hw_timer(port)

        output = self.read_output(port, output_bytes)
        return output, cycles

    def _poll_completion(self, port: CsrPort, *, timeout_s: float) -> None:
        """Poll COMPLETION_COUNT exactly like the DDR-backed path -- the completion-count register
        is part of the common `dla_dma_csr.sv` block and increments regardless of which datapath
        (DDR-backed or streaming) produced the token (`coredla_csr_handshake.py`'s module
        docstring)."""
        baseline = port.csr_read32(DLA_DMA_CSR_OFFSET_COMPLETION_COUNT) & _MASK32
        self.handshake.wait_for_done(port, baseline, timeout_s=timeout_s)
