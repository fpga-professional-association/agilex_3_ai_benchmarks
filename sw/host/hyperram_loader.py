#!/usr/bin/env python3
"""Guard-banded HyperRAM loader + parity-gated single inference (Track DRV, DDR-backed path).

This is step 3 of the task brief: "write config-words + weights + input into HyperRAM at
GUARD-BANDED, row-aligned bases (respect the wound law -- no write base abutting live data), run
the handshake (CONFIG_BASE_ADDR -> CONFIG_RANGE_MINUS_TWO -> INPUT_OUTPUT_BASE_ADDR **last=trigger**
-> poll COMPLETION_COUNT), read the output region, PARITY-CHECK it against a provided reference
(refuse to report on mismatch)."

It is pure orchestration glue over three already-resolved/tested pieces:
  - `aot_layout.HyperRamLayout` -- WHERE each buffer lives (guard-banded, from the real compiler's
    own `ddr_buffer_info_*.txt`, see aot_layout.py's module docstring).
  - `coredla_csr_handshake.CoreDlaCsrHandshake` -- the start/done CSR sequence + on-chip hw_timer.
  - any object satisfying `DdrPort` (`write_ddr`/`read_ddr`) -- in production
    `coredla_csr_handshake.SystemConsoleTransport`; in tests, a mock (see
    tests/test_hyperram_loader.py -- fully board-free).

Nothing here talks to hardware directly, so the whole module is unit-testable off-board.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from aot_layout import HyperRamLayout, build_inference_job
from coredla_csr_handshake import CoreDlaCsrHandshake, CsrPort


@runtime_checkable
class DdrPort(Protocol):
    """Block read/write to the HyperRAM AXI4 window. Addresses are RAW byte addresses (base 0x0,
    no CSR-window offset added) -- matches `coredla_csr_handshake.SystemConsoleTransport.write_ddr`/
    `read_ddr`."""

    def write_ddr(self, addr: int, data: bytes) -> None: ...
    def read_ddr(self, addr: int, nbytes: int) -> bytes: ...


class ParityError(RuntimeError):
    """Hardware output does not match the provided reference. Refuse to report the run rather than
    silently accepting a mismatched result (AGENTS.md; docs/onboard_benchmark_plan.md §6: `clk_dla`
    is currently timing-marginal, so every run must be parity-gated)."""


@dataclass
class InferenceResult:
    output: bytes
    cycles: int
    completion_count: int


def load_config_filter(port: DdrPort, layout: HyperRamLayout, config_filter_bytes: bytes) -> None:
    """Write the ONE contiguous config+filter(+bias/scale) blob at `layout.config_base_addr`.

    Guard-banding is already baked into `layout` (`aot_layout.resolve_hyperram_layout`): this write's
    base has >= `layout.guard_bytes` of dead space below it, so the write-wound law
    (docs/coredla_hyperram_onboard_findings.md §7) cannot clobber anything live.
    """
    if len(config_filter_bytes) != layout.config_filter_write_bytes:
        raise ValueError(
            f"config+filter blob is {len(config_filter_bytes)} bytes, layout expects "
            f"{layout.config_filter_write_bytes} (see aot_layout.DdrBufferLayout.config_filter_buffer_size)")
    port.write_ddr(layout.config_base_addr, config_filter_bytes)


def load_input(port: DdrPort, layout: HyperRamLayout, input_bytes: bytes) -> None:
    """Write the input tensor at `layout.input_addr` (also the GO-trigger CSR value). Guard-banded
    below its base for the same reason as `load_config_filter`. The output region immediately after
    it is NOT written by the host -- the hardware writes it during inference."""
    if len(input_bytes) != layout.input_bytes:
        raise ValueError(
            f"input tensor is {len(input_bytes)} bytes, layout expects {layout.input_bytes} "
            "(see aot_layout.DdrBufferLayout.single_input())")
    port.write_ddr(layout.input_addr, input_bytes)


def run_one_inference(port: CsrPort, layout: HyperRamLayout, *, timeout_s: float = 30.0,
                      handshake: CoreDlaCsrHandshake | None = None) -> InferenceResult:
    """Run the CSR start/done handshake (hw_timer-bracketed) and read the output back.

    Assumes `load_config_filter`/`load_input` already ran (or config+weights are unchanged from a
    prior inference of the same model -- the vendor runtime rewrites CONFIG_BASE_ADDR/RANGE on every
    call regardless, see `CoreDlaCsrHandshake.run_inference_timed`, so this is safe either way).
    `port` must satisfy both `CsrPort` and `DdrPort` (e.g. `SystemConsoleTransport`).
    """
    handshake = handshake or CoreDlaCsrHandshake()
    job = build_inference_job(layout)
    completion, cycles = handshake.run_inference_timed(port, job, timeout_s=timeout_s)
    output = port.read_ddr(layout.output_addr, layout.output_bytes)
    return InferenceResult(output=output, cycles=cycles, completion_count=completion)


def run_one_inference_with_parity(port, layout: HyperRamLayout, *, reference_output: bytes,
                                  timeout_s: float = 30.0,
                                  handshake: CoreDlaCsrHandshake | None = None) -> InferenceResult:
    """`run_one_inference` + a hard parity gate against `reference_output`."""
    result = run_one_inference(port, layout, timeout_s=timeout_s, handshake=handshake)
    if result.output != reference_output:
        raise ParityError(
            f"hardware output ({len(result.output)} bytes) does not match the reference "
            f"({len(reference_output)} bytes) -- refusing to report this run. Re-check clk_dla "
            "timing closure (docs/coredla_hyperram_onboard_findings.md §3c/§6) before retrying.")
    return result


def load_and_run(port, layout: HyperRamLayout, *, config_filter_bytes: bytes, input_bytes: bytes,
                 reference_output: bytes | None = None, timeout_s: float = 30.0,
                 handshake: CoreDlaCsrHandshake | None = None) -> InferenceResult:
    """One full HyperRAM-backed inference end to end: guard-banded load -> handshake -> read ->
    (optional) parity check. `port` must satisfy both `CsrPort` and `DdrPort`.

    Pass `reference_output` (the CPU-INT8 / expected raw output bytes for this exact input) to
    enforce the parity gate (raises `ParityError` on any mismatch instead of returning); omit it only
    for exploratory/non-scored runs.
    """
    load_config_filter(port, layout, config_filter_bytes)
    load_input(port, layout, input_bytes)
    if reference_output is not None:
        return run_one_inference_with_parity(port, layout, reference_output=reference_output,
                                             timeout_s=timeout_s, handshake=handshake)
    return run_one_inference(port, layout, timeout_s=timeout_s, handshake=handshake)
