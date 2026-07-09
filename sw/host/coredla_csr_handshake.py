#!/usr/bin/env python3
"""CoreDLA CSR start/done/config handshake — the sequence smoke_infer.py's
`SystemConsoleTransport.run_inference()` left as `NotImplementedError` (PLAN §9 PH1).

This module fills that gap. Unlike the placeholder in smoke_infer.py, the sequence below is NOT
guessed: every register offset, every bit, and the exact write order are transcribed from the FPGA
AI Suite 2026.1.1 runtime source shipped inside the `alterafpga/fpgaaisuite:2026.1.1-quartus`
Docker image. The vendor OpenVINO-FPGA runtime (`libcoreDlaRuntimePlugin`) drives precisely this
sequence; it is discoverable, not unknown. Citations (paths inside the image):

    RTL (defines the hardware contract):
      $COREDLA_ROOT/fpga/dma/rtl/dla_dma_csr.sv
      $COREDLA_ROOT/fpga/dma/dual_inc/dla_dma_constants.svh      (numeric offsets)
      $COREDLA_ROOT/fpga/platform_adapter/rtl/dla_platform_csr_adapter.sv

    Host runtime (drives the contract):
      $COREDLA_ROOT/runtime/coredla_device/src/coredla_batch_job.cpp      (StartDla)
      $COREDLA_ROOT/runtime/coredla_device/src/coredla_device.cpp         (WaitForDla, ctor)
      $COREDLA_ROOT/runtime/coredla_device/src/device_memory_allocator.cpp(intermediate addr)
      $COREDLA_ROOT/runtime/coredla_device/mmd/system_console/mmd_wrapper.cpp  (JTAG CSR I/O)
      $COREDLA_ROOT/runtime/coredla_device/mmd/system_console/system_console_script.tcl

  where $COREDLA_ROOT = /opt/altera/fpga_ai_suite/ubuntu/dla.

--------------------------------------------------------------------------------------------------
THE HANDSHAKE (DDR-backed, non-streaming path — what the agx3c_jtag EMIF+CSR design exposes)
--------------------------------------------------------------------------------------------------
All CSR offsets are BYTE offsets inside the CoreDLA CSR window. The System Console JTAG master
reaches them at absolute address (DLA_CSR_BASE + offset), DLA_CSR_BASE = 0x8000_0000
(mmd_wrapper.cpp:69 `DLA_CSR_BASE_ADDRESS`, added in write_to_csr()/read_from_csr()). Inside the
RTL the byte offset is compared as a WORD address (offset/4) — see dla_dma_csr.sv address decode —
so the host writes the byte offset directly and the hardware divides by 4 itself. Do NOT pre-scale.

Preconditions (host must have already placed these in DDR/global memory via write_ddr):
  - the compiled network's config-words blob (and, immediately after it, the filter/weights) at
    `config_base_addr`. "config immediately followed by filter" is required by the allocator
    (device_memory_allocator.cpp AllocatePrivateBuffer comment).
  - the input tensor at `input_addr`; the output region reserved immediately after the input
    (allocator: "output must come immediately after input").
  - intermediate scratch buffer at `intermediate_addr` (the stock runtime uses 0).

Start (coredla_batch_job.cpp::StartDla, the `!disableExternalMemory_ && !enableIstream_` path):
  0. baseline = ReadFromCsr(COMPLETION_COUNT)            # remember how many jobs finished so far
  1. WriteToCsr(INTERMEDIATE_BASE_ADDR, intermediate_addr)   # device_memory_allocator.cpp:44
  2. WriteToCsr(CONFIG_BASE_ADDR,       config_base_addr)    # coredla_batch_job.cpp:136
  3. WriteToCsr(CONFIG_RANGE_MINUS_TWO, config_words - 2)    # coredla_batch_job.cpp:140
        config_words = total_config_bytes // CONFIG_READER_DATA_BYTES, CONFIG_READER_DATA_BYTES = 8
        "minus two": the HW range counter is a down-counter that ends at -1 and uses the sign bit
        as the terminator (comment at coredla_batch_job.cpp:138-139).
  4. WriteToCsr(INPUT_OUTPUT_BASE_ADDR, input_addr)          # coredla_batch_job.cpp:150
        *** THIS WRITE IS THE "GO" ***  In dla_dma_csr.sv the address decode raises
        `enqueue_descriptor` for offset INPUT_OUTPUT_BASE_ADDR (line ~594); after the AXI write
        response the FSM jumps to STATE_DESCRIPTOR (line ~827), which pushes 8 words into the
        descriptor queue = one unit of work = one inference. There is no separate "start bit";
        writing the I/O base address IS the trigger. It must be written LAST.

Done (coredla_device.cpp::WaitForDla, `runtimePolling_` branch, lines 287-303):
  5. poll ReadFromCsr(COMPLETION_COUNT) until it != baseline (32-bit free-running counter;
     dla_dma_csr.sv: `completion_count <= completion_count + i_token_done`, i_token_done pulses
     once when the feature writer finishes a job). Compare with wraparound. Time out otherwise.

Interrupts are OPTIONAL on the JTAG/System-Console path: RegisterISR() throws "System Console
plugin requires polling" (mmd_wrapper.cpp), and completion_count increments regardless of the
interrupt mask (the mask only gates the level-sensitive o_interrupt_level line, dla_dma_csr.sv
interrupt block). So polling completion_count is self-sufficient and is exactly what the vendor's
own polling path does. `configure_interrupts()` below mirrors the constructor's clear+enable
(coredla_device.cpp:95-98) for callers that want the sticky INTERRUPT_CONTROL.done bit too; it is
not required for correctness of a single smoke inference.

--------------------------------------------------------------------------------------------------
WIRING INTO smoke_infer.py (no edit to smoke_infer.py required)
--------------------------------------------------------------------------------------------------
smoke_infer.py's `InferenceTransport.run_inference(*, timeout_s)` takes no addresses, so the job
parameters are attached to the transport. Two ways to use this module:

  (A) Drop-in transport. Import the subclass here instead of smoke_infer's stub:
          from coredla_csr_handshake import SystemConsoleTransport, InferenceJob
          job = InferenceJob(config_base_addr=..., total_config_bytes=..., input_addr=0x0,
                             intermediate_addr=0x0)
          t = SystemConsoleTransport(sof_path="top.sof", job=job)
          t.open()                      # spawns `system-console`, claims the two masters
          smoke_infer.smoke_infer(t, input_bytes=..., input_addr=job.input_addr,
                                  output_addr=..., output_bytes=...)
      smoke_infer calls t.run_inference(timeout_s=...), which runs the sequence above.

  (B) Handshake only, over any object exposing csr_read32/csr_write32 (e.g. smoke_infer's own
      MockTransport, or a future scoreboard transport):
          from coredla_csr_handshake import CoreDlaCsrHandshake, InferenceJob
          CoreDlaCsrHandshake().run_inference(port, job, timeout_s=30.0)

The sequence logic (CoreDlaCsrHandshake) is pure and unit-tested with a mock port
(tests/test_coredla_csr_handshake.py) — no board, no system-console.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


# =============================================================================================
# CoreDLA DMA CSR register map — byte offsets, verbatim from dla_dma_constants.svh (see citations
# in the module docstring). These are the offsets the runtime passes to WriteToCsr/ReadFromCsr;
# they are byte offsets within the 0x8000_0000 CSR window.
# =============================================================================================
DLA_DMA_CSR_OFFSET_INTERRUPT_CONTROL = 512        # 0x200  W1C: bit0=error, bit1=done
DLA_DMA_CSR_OFFSET_INTERRUPT_MASK = 516           # 0x204  bit0=error, bit1=done
DLA_DMA_CSR_OFFSET_CONFIG_BASE_ADDR = 528         # 0x210  config-reader base address in DDR
DLA_DMA_CSR_OFFSET_CONFIG_RANGE_MINUS_TWO = 532   # 0x214  (#config words / 8) - 2
DLA_DMA_CSR_OFFSET_INPUT_OUTPUT_BASE_ADDR = 536   # 0x218  *** write triggers one inference ***
DLA_DMA_CSR_OFFSET_DESC_DIAGNOSTICS = 540         # 0x21c  bit0=overflow,1=almost_full,2=out_of_inf
DLA_DMA_CSR_OFFSET_INTERMEDIATE_BASE_ADDR = 544   # 0x220  intermediate scratch base in DDR
DLA_DMA_CSR_OFFSET_COMPLETION_COUNT = 548         # 0x224  free-running #jobs completed (poll this)
DLA_DMA_CSR_OFFSET_IP_RESET = 552                 # 0x228  write nonzero -> soft reset the IP
DLA_CSR_OFFSET_READY_STREAMING_IFACE = 556        # 0x22c  input-streaming "go" (istream path)
DLA_DMA_CSR_OFFSET_CLOCKS_ACTIVE_LO = 576         # 0x240  perf counter (busy clocks) low word
DLA_DMA_CSR_OFFSET_CLOCKS_ACTIVE_HI = 580         # 0x244
DLA_DMA_CSR_OFFSET_LICENSE_FLAG = 608             # 0x260  read: licensed?

# Interrupt control/mask bit positions (dla_dma_constants.svh:97-98).
DLA_DMA_CSR_INTERRUPT_ERROR_BIT = 0
DLA_DMA_CSR_INTERRUPT_DONE_BIT = 1
ALL_INTERRUPTS_MASK = (1 << DLA_DMA_CSR_INTERRUPT_ERROR_BIT) | (1 << DLA_DMA_CSR_INTERRUPT_DONE_BIT)

# Descriptor-queue diagnostics bits (dla_dma_constants.svh:101-103).
DLA_DMA_CSR_DESC_DIAGNOSTICS_OVERFLOW_BIT = 0
DLA_DMA_CSR_DESC_DIAGNOSTICS_ALMOST_FULL_BIT = 1
DLA_DMA_CSR_DESC_DIAGNOSTICS_OUT_OF_INFERENCES_BIT = 2

# Width of the config-reader input port in bytes (coredla_batch_job.cpp:19). The CONFIG_RANGE CSR
# is expressed in units of this many bytes.
CONFIG_READER_DATA_BYTES = 8

# System Console absolute base of the CoreDLA CSR window (mmd_wrapper.cpp:69). The runtime adds
# this to every byte offset before issuing master_write_32/master_read_32. Matches smoke_infer's
# DLA_CSR_OFFSET.
DLA_CSR_BASE_ADDRESS = 0x8000_0000

_MASK32 = 0xFFFF_FFFF


@runtime_checkable
class CsrPort(Protocol):
    """The minimal control-plane surface the handshake needs: single 32-bit CSR word access.

    smoke_infer.py's InferenceTransport (and its MockTransport) already satisfy this, as does any
    future transport. Addresses are the byte offsets defined above (NOT pre-scaled, NOT with the
    0x8000_0000 base already added — the transport's csr_* implementation owns that translation,
    exactly like mmd_wrapper.cpp's write_to_csr adds DLA_CSR_BASE_ADDRESS)."""

    def csr_write32(self, addr: int, value: int) -> None: ...
    def csr_read32(self, addr: int) -> int: ...


@dataclass
class InferenceJob:
    """Everything the start sequence needs beyond the input tensor bytes themselves.

    These correspond one-to-one to the CSR writes in coredla_batch_job.cpp::StartDla. For a real
    run they come from the CoreDLA compiler output (the `.bin`): `total_config_bytes` is the size
    of the config-words + on-chip-ROM blob the compiler emits, and the DDR addresses come from the
    device memory allocator's layout. For a fixed smoke design they are constants you pin down once.
    """

    config_base_addr: int          # DDR byte address of the config-reader blob (config + weights)
    total_config_bytes: int        # size in bytes of the config-words blob (for RANGE_MINUS_TWO)
    input_addr: int = 0x0          # DDR byte address of the input tensor (also the "go" value)
    intermediate_addr: int = 0x0   # DDR byte address of intermediate scratch (stock runtime: 0)

    def config_range_minus_two(self) -> int:
        """(#config words / CONFIG_READER_DATA_BYTES) - 2, matching coredla_batch_job.cpp:140.

        The hardware range register is a down-counter terminating at -1 (sign bit = done), hence
        the -2. Raises if the config blob is too small to form a valid (>=0) range."""
        words = self.total_config_bytes // CONFIG_READER_DATA_BYTES
        rng = words - 2
        if self.total_config_bytes % CONFIG_READER_DATA_BYTES:
            raise ValueError(
                f"total_config_bytes={self.total_config_bytes} is not a multiple of "
                f"CONFIG_READER_DATA_BYTES={CONFIG_READER_DATA_BYTES}")
        if rng < 0:
            raise ValueError(
                f"config blob too small: {words} words gives range {rng} (< 0); need >= 2 words")
        return rng & _MASK32


class InferenceError(RuntimeError):
    """Raised when the descriptor queue overflowed, the IP reports out-of-inferences, or the job
    did not complete within the timeout."""


class CoreDlaCsrHandshake:
    """Pure CoreDLA CSR start/done sequence over a CsrPort. No hardware I/O of its own — it only
    calls port.csr_read32 / port.csr_write32, so it is fully unit-testable with a mock port.

    Mirrors the vendor runtime: StartDla (coredla_batch_job.cpp) for the writes,
    WaitForDla polling branch (coredla_device.cpp) for the completion poll."""

    def __init__(self, *, poll_interval_s: float = 0.001):
        self.poll_interval_s = poll_interval_s

    # -- optional: mirror the device constructor's interrupt clear+enable ---------------------
    def configure_interrupts(self, port: CsrPort) -> None:
        """Clear any stale interrupt status then unmask done+error. Optional for the polling path
        (see module docstring); provided for parity with coredla_device.cpp:95-98."""
        port.csr_write32(DLA_DMA_CSR_OFFSET_INTERRUPT_CONTROL, ALL_INTERRUPTS_MASK)  # W1C clear
        port.csr_write32(DLA_DMA_CSR_OFFSET_INTERRUPT_MASK, ALL_INTERRUPTS_MASK)

    # -- the start half -----------------------------------------------------------------------
    def start(self, port: CsrPort, job: InferenceJob) -> int:
        """Issue the four config writes and the trigger write, in the exact vendor order. Returns
        the pre-trigger COMPLETION_COUNT baseline that `wait_for_done` needs.

        The I/O-base write is LAST and is the trigger — see module docstring."""
        baseline = port.csr_read32(DLA_DMA_CSR_OFFSET_COMPLETION_COUNT) & _MASK32

        # 1. intermediate scratch base (device_memory_allocator.cpp:44)
        port.csr_write32(DLA_DMA_CSR_OFFSET_INTERMEDIATE_BASE_ADDR, job.intermediate_addr & _MASK32)
        # 2. config-reader base (coredla_batch_job.cpp:136)
        port.csr_write32(DLA_DMA_CSR_OFFSET_CONFIG_BASE_ADDR, job.config_base_addr & _MASK32)
        # 3. config-reader length, in (8-byte) words minus two (coredla_batch_job.cpp:140)
        port.csr_write32(DLA_DMA_CSR_OFFSET_CONFIG_RANGE_MINUS_TWO, job.config_range_minus_two())
        # 4. GO: writing the input/output base address enqueues one descriptor = one inference
        #    (coredla_batch_job.cpp:150; dla_dma_csr.sv enqueue_descriptor -> STATE_DESCRIPTOR)
        port.csr_write32(DLA_DMA_CSR_OFFSET_INPUT_OUTPUT_BASE_ADDR, job.input_addr & _MASK32)
        return baseline

    # -- the done half ------------------------------------------------------------------------
    def wait_for_done(self, port: CsrPort, baseline: int, *, timeout_s: float = 30.0) -> int:
        """Poll COMPLETION_COUNT until it advances past `baseline` (32-bit wrap-safe), exactly like
        coredla_device.cpp:291-302's polling loop. Returns the observed completion count.

        Raises InferenceError on timeout or if the descriptor queue overflowed."""
        deadline = time.monotonic() + timeout_s
        baseline &= _MASK32
        while True:
            current = port.csr_read32(DLA_DMA_CSR_OFFSET_COMPLETION_COUNT) & _MASK32
            if ((current - baseline) & _MASK32) >= 1:
                return current
            self._check_descriptor_health(port)
            if time.monotonic() > deadline:
                diag = port.csr_read32(DLA_DMA_CSR_OFFSET_DESC_DIAGNOSTICS)
                raise InferenceError(
                    f"CoreDLA inference did not complete within {timeout_s}s "
                    f"(COMPLETION_COUNT stuck at {baseline}, DESC_DIAGNOSTICS=0x{diag:08x}). "
                    "Check the bitstream is programmed, config/weights are resident in DDR, and "
                    "the I/O-base write actually reached the CSR.")
            if self.poll_interval_s:
                time.sleep(self.poll_interval_s)

    @staticmethod
    def _check_descriptor_health(port: CsrPort) -> None:
        diag = port.csr_read32(DLA_DMA_CSR_OFFSET_DESC_DIAGNOSTICS)
        if diag & (1 << DLA_DMA_CSR_DESC_DIAGNOSTICS_OVERFLOW_BIT):
            raise InferenceError(
                "CoreDLA descriptor queue overflowed (DESC_DIAGNOSTICS.overflow) — the host "
                "enqueued more jobs than the hardware queue can hold before draining them.")
        if diag & (1 << DLA_DMA_CSR_DESC_DIAGNOSTICS_OUT_OF_INFERENCES_BIT):
            raise InferenceError(
                "CoreDLA reports out-of-inferences (DESC_DIAGNOSTICS.out_of_inferences) — the "
                "licensed inference count for this bitstream is exhausted.")

    # -- convenience: full single-inference handshake -----------------------------------------
    def run_inference(self, port: CsrPort, job: InferenceJob, *, timeout_s: float = 30.0) -> int:
        """start() then wait_for_done(). Returns the post-run COMPLETION_COUNT."""
        baseline = self.start(port, job)
        return self.wait_for_done(port, baseline, timeout_s=timeout_s)


# =================================================================================================
# Drop-in transport for smoke_infer.py.
#
# We subclass smoke_infer.InferenceTransport when it is importable so this is a true drop-in
# (isinstance-compatible); otherwise we fall back to a local ABC-free base so the module still
# imports standalone (e.g. in isolation, or if sw/host is not yet on sys.path). Either way the
# handshake logic above is what does the work.
# =================================================================================================
try:  # pragma: no cover - import wiring, exercised implicitly by tests
    from smoke_infer import InferenceTransport as _BaseTransport
except Exception:  # pragma: no cover
    _BaseTransport = object


class SystemConsoleTransport(_BaseTransport):  # type: ignore[misc,valid-type]
    """Board transport that speaks Intel/Altera **System Console** over JTAG, issuing the exact Tcl
    command forms from mmd_wrapper.cpp / system_console_script.tcl, and whose `run_inference` runs
    the vendor CSR handshake (CoreDlaCsrHandshake) instead of raising NotImplementedError.

    Not exercised in CI (needs the board, a programmed top.sof, and a `system-console` install).
    Construction does NOT touch hardware; call `open()` to spawn system-console and claim the two
    JTAG master services, `close()` when done (or use as a context manager). The CSR/DDR command
    forms are transcribed verbatim so a bring-up engineer can trust them; only the subprocess
    plumbing (`_send`) is left as the single hardware seam.
    """

    # Master service offsets/ranges, from system_console_script.tcl (the same constants
    # smoke_infer.py documents).
    EMIF_OFFSET = 0x0000_0000
    EMIF_RANGE = 0x0800_0000
    DLA_CSR_BASE = DLA_CSR_BASE_ADDRESS      # 0x8000_0000
    DLA_CSR_RANGE = 0x0000_0900

    def __init__(self, sof_path: str, *, job: InferenceJob | None = None,
                 jtag_path: str = "*jtag*master*", poll_interval_s: float = 0.001):
        self.sof_path = sof_path
        self.jtag_path = jtag_path
        self.job = job
        self._handshake = CoreDlaCsrHandshake(poll_interval_s=poll_interval_s)
        self._proc = None  # set by open()

    # -- lifecycle (hardware seam) ------------------------------------------------------------
    def open(self) -> "SystemConsoleTransport":  # pragma: no cover - needs a board
        """Spawn `system-console`, load the .sof, and claim the EMIF-DDR and DLA-CSR master
        services (the procs claim_emif_ddr_service / claim_dla_csr_service in
        system_console_script.tcl). Left as the single hardware-dependent method."""
        raise NotImplementedError(
            "SystemConsoleTransport.open() spawns `system-console` on the AXC3000 board — wire it "
            "to a subprocess that sources system_console_script.tcl, runs load_sof / "
            "claim_emif_ddr_service / claim_dla_csr_service, then feeds _send(). This is the only "
            "board-dependent seam; all CSR sequence logic is in CoreDlaCsrHandshake and is tested "
            "off-board. See docs/coredla_csr_handshake_findings.md.")

    def close(self) -> None:  # pragma: no cover - needs a board
        if self._proc is not None:
            self._send("close_service master $::g_dla_csr_service")
            self._send("close_service master $::g_emif_ddr_service")
            self._proc = None

    def __enter__(self):  # pragma: no cover
        return self.open()

    def __exit__(self, *exc):  # pragma: no cover
        self.close()

    def _send(self, tcl: str) -> str:  # pragma: no cover - needs a board
        """Write one Tcl line to the system-console stdin and read back its reply. The single
        hardware seam; every csr_*/ddr_* method below is expressed in terms of it."""
        raise NotImplementedError("connect _send() to the system-console subprocess in open()")

    # -- CsrPort / InferenceTransport surface -------------------------------------------------
    def csr_write32(self, addr: int, value: int) -> None:  # pragma: no cover - needs a board
        # mmd_wrapper.cpp write_to_csr: addr += DLA_CSR_BASE_ADDRESS; master_write_32 <svc> a d
        self._send(f"master_write_32 $::g_dla_csr_service "
                   f"0x{(self.DLA_CSR_BASE + addr) & _MASK32:08x} 0x{value & _MASK32:08x}")

    def csr_read32(self, addr: int) -> int:  # pragma: no cover - needs a board
        # mmd_wrapper.cpp read_from_csr: addr += DLA_CSR_BASE_ADDRESS; master_read_32 <svc> a 1
        reply = self._send(f"master_read_32 $::g_dla_csr_service "
                           f"0x{(self.DLA_CSR_BASE + addr) & _MASK32:08x} 1")
        return int(reply.strip().split()[-1], 0) & _MASK32

    def write_ddr(self, addr: int, data: bytes) -> None:  # pragma: no cover - needs a board
        # system_console_script.tcl uses master_write_from_file for block DDR writes.
        raise NotImplementedError(
            "write_ddr: stage `data` to a temp file and issue "
            "`master_write_from_file $::g_emif_ddr_service <tmp> 0x{addr:08x}` (system-console has "
            "no bulk in-line write); implement alongside _send() during bring-up.")

    def read_ddr(self, addr: int, nbytes: int) -> bytes:  # pragma: no cover - needs a board
        raise NotImplementedError(
            "read_ddr: issue `master_read_to_file $::g_emif_ddr_service <tmp> 0x{addr:08x} "
            "{nbytes}` then read the temp file back; implement alongside _send() during bring-up.")

    def run_inference(self, *, timeout_s: float = 30.0) -> None:  # pragma: no cover - needs a board
        """Run the vendor CoreDLA CSR handshake for `self.job`. This is what smoke_infer calls."""
        if self.job is None:
            raise ValueError(
                "SystemConsoleTransport.run_inference needs an InferenceJob; pass job=... to the "
                "constructor (config_base_addr, total_config_bytes, input_addr, intermediate_addr).")
        self._handshake.run_inference(self, self.job, timeout_s=timeout_s)
