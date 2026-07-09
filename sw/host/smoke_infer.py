#!/usr/bin/env python3
"""Hostless-JTAG smoke inference — push one input tensor, read one output back (issue #7, PLAN §9 PH1).

Targets the FPGA AI Suite `agx3c_jtag` example design's own JTAG-Avalon memory map, NOT the
scoreboard/HyperRAM record-replay path `sw/host/transport.py` drives (that is issue #17's PLAN §6
harness, a different platform). The address map and System Console command sequence below are taken
verbatim from the AI Suite 2026.1.1 runtime source shipped in the `fpgaaisuite` Docker image
(`docs/toolchain.md`), not guessed:

    /opt/altera/fpga_ai_suite/ubuntu/dla/runtime/coredla_device/mmd/system_console/system_console_script.tcl
    /opt/altera/fpga_ai_suite/ubuntu/dla/runtime/coredla_device/mmd/system_console/mmd_wrapper.cpp

    g_const_master_offset_emif = 0x0,         range 0x0800_0000  -> "DDR" global memory window
    g_const_master_offset_dla  = 0x8000_0000, range 0x900        -> CoreDLA CSR (0x0000-0x07FF)
                                                                     + hw timer (0x0800-0x08FF)
    master_write_32 <csr_service> <addr> <data>        # single CSR register write
    master_read_32  <csr_service> <addr> 1             # single CSR register read
    master_write_from_file <ddr_service> <tmpfile> <addr>   # DDR block write
    master_read_to_file    <ddr_service> <tmpfile> <addr> <length>  # DDR block read

What this script does NOT know (and does not fake): the CoreDLA CSR bit(s) that actually kick off
one inference and signal completion are internal to the vendor's OpenVINO FPGA plugin
(`libcoreDlaRuntimePlugin.so`) and are not documented in the files this repo has access to.
`SystemConsoleTransport.run_inference()` raises `NotImplementedError` for that reason — filling it
in is real board-bring-up work (observe the plugin's actual CSR pokes, e.g. with a JTAG/SignalTap
trace, or a vendor CSR-map doc if one surfaces) and is out of scope for what this session could
verify without hardware. See docs/board_bringup.md.

    python smoke_infer.py --sof top.sof --input ds_cnn_input.bin --input-addr 0x0 \\
        --output-addr 0x1000 --output-bytes 4 --expected-argmax 3 \\
        --out results/ph1_dscnn_hostless_jtag_smoke.json
"""

from __future__ import annotations

import argparse
import json
import sys
from abc import ABC, abstractmethod
from pathlib import Path

DEVICE = "A3CY100BM16AE7S"
BOARD = "Arrow AXC3000"

# JTAG-Avalon memory map, from system_console_script.tcl (verbatim, see module docstring).
EMIF_OFFSET = 0x0000_0000
EMIF_RANGE = 0x0800_0000
DLA_CSR_OFFSET = 0x8000_0000
DLA_CSR_RANGE = 0x0000_0900
DLA_TIMER_OFFSET = 0x8000_0800


class InferenceTransport(ABC):
    """One inference's worth of DDR block I/O + CSR access, over JTAG (control plane only, PLAN §8
    method E — never used here as a timed data path; this script measures correctness, not rate)."""

    @abstractmethod
    def write_ddr(self, addr: int, data: bytes) -> None: ...

    @abstractmethod
    def read_ddr(self, addr: int, nbytes: int) -> bytes: ...

    @abstractmethod
    def csr_write32(self, addr: int, value: int) -> None: ...

    @abstractmethod
    def csr_read32(self, addr: int) -> int: ...

    @abstractmethod
    def run_inference(self, *, timeout_s: float = 30.0) -> None:
        """Kick one inference and block until CoreDLA signals completion."""


class MockTransport(InferenceTransport):
    """In-memory model for unit tests — no board, no system-console. `run_inference` is a no-op:
    the test sets up `mem` so the expected output is already at `output_addr` (this script's own
    argmax/compare logic is what's under test here, not the vendor CSR handshake)."""

    def __init__(self, mem_size: int = EMIF_RANGE):
        self.mem = bytearray(mem_size)
        self.csr: dict[int, int] = {}
        self.inference_calls = 0

    def write_ddr(self, addr: int, data: bytes) -> None:
        self.mem[addr:addr + len(data)] = data

    def read_ddr(self, addr: int, nbytes: int) -> bytes:
        return bytes(self.mem[addr:addr + nbytes])

    def csr_write32(self, addr: int, value: int) -> None:
        self.csr[addr] = value & 0xFFFFFFFF

    def csr_read32(self, addr: int) -> int:
        return self.csr.get(addr, 0)

    def run_inference(self, *, timeout_s: float = 30.0) -> None:
        self.inference_calls += 1


class SystemConsoleTransport(InferenceTransport):
    """Drives Intel/Altera **System Console** over JTAG, issuing the exact Tcl command forms found
    in the AI Suite 2026.1.1 runtime's own `mmd_wrapper.cpp` (module docstring). Not exercised in
    CI — requires the board, a programmed `top.sof`, and a `system-console` install.

    Construction claims both master services (`claim_emif_ddr_service` / `claim_dla_csr_service`,
    same procs as `system_console_script.tcl`); `run_inference` is intentionally unimplemented (see
    module docstring) rather than guessed.
    """

    def __init__(self, sof_path: str, jtag_path: str = "*jtag*master*"):
        self.sof_path = sof_path
        self.jtag_path = jtag_path
        raise NotImplementedError(
            "SystemConsoleTransport is wired up during AXC3000 board bring-up: needs a "
            "programmed top.sof, a `system-console` install, and (per this module's docstring) "
            "the CoreDLA CSR start/done bit layout reverse-engineered from the vendor runtime "
            "plugin's behavior. Use MockTransport off-board.")

    def write_ddr(self, addr: int, data: bytes) -> None: ...        # pragma: no cover
    def read_ddr(self, addr: int, nbytes: int) -> bytes: ...        # pragma: no cover
    def csr_write32(self, addr: int, value: int) -> None: ...       # pragma: no cover
    def csr_read32(self, addr: int) -> int: ...                     # pragma: no cover
    def run_inference(self, *, timeout_s: float = 30.0) -> None: ...  # pragma: no cover


def argmax_int8(data: bytes) -> int:
    """argmax over a buffer of signed INT8 logits (CoreDLA NoSoftmax arch output, PLAN §9 PH1)."""
    if not data:
        raise ValueError("empty output tensor")
    signed = [b - 256 if b >= 128 else b for b in data]
    return max(range(len(signed)), key=lambda i: signed[i])


def smoke_infer(transport: InferenceTransport, *, input_bytes: bytes, input_addr: int,
                 output_addr: int, output_bytes: int, timeout_s: float = 30.0) -> dict:
    """Push one input tensor, run one inference, read the output tensor back. Returns
    {"output": bytes, "argmax": int}."""
    transport.write_ddr(input_addr, input_bytes)
    transport.run_inference(timeout_s=timeout_s)
    out = transport.read_ddr(output_addr, output_bytes)
    return {"output": out, "argmax": argmax_int8(out)}


def build_result(run: dict, *, model: str, arch_file: str, date: str, subject: str,
                  fclk_mhz: float | None = None, expected_argmax: int | None = None,
                  tool_versions: dict | None = None, utilization: dict | None = None,
                  bitstream_sha256: str | None = None) -> dict:
    """Assemble a `results/` JSON conforming to results/schema/result.schema.json, kind=measured,
    level=PH1 (PLAN §9 PH1)."""
    matches = expected_argmax is not None and run["argmax"] == expected_argmax
    config: dict = {
        "device": DEVICE,
        "board": BOARD,
        "model": model,
        "arch_file": arch_file,
        "quantization": "int8-nncf-ptq",
        # No feed_method: the schema's enum (PLAN §8) is A-D, all rate-measurement feed paths.
        # This is a single-record functional check over JTAG (PLAN §8 method E, control/readback
        # only) -- not a rate measurement, so it deliberately doesn't claim one of A-D.
        "tool_versions": tool_versions or {},
    }
    if fclk_mhz is not None:
        config["fclk_mhz"] = fclk_mhz
    if utilization is not None:
        config["utilization"] = utilization
    if bitstream_sha256 is not None:
        config["bitstream_sha256"] = bitstream_sha256
    return {
        "kind": "measured",
        "level": "PH1",
        "subject": subject,
        "date": date,
        "plan_ref": "§9 PH1",
        "config": config,
        "metrics": {
            "n_records": 1,
            "accuracy_top1": 1.0 if matches else 0.0,
        },
        "notes": (
            f"argmax={run['argmax']}"
            + (f", expected={expected_argmax}, matches_cpu_int8={matches}"
               if expected_argmax is not None else ", no --expected-argmax given")
            + "; single-record hostless-JTAG smoke test, emitted by sw/host/smoke_infer.py "
              "(PLAN §8 method E: control/readback only, never a rate measurement)."),
    }


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--sof", required=True, help="programmed bitstream (for SystemConsoleTransport)")
    ap.add_argument("--input", required=True, help="path to the packed INT8 input tensor")
    ap.add_argument("--input-addr", type=lambda s: int(s, 0), default=EMIF_OFFSET)
    ap.add_argument("--output-addr", type=lambda s: int(s, 0), required=True)
    ap.add_argument("--output-bytes", type=int, required=True)
    ap.add_argument("--model", default="ds-cnn-kws")
    # Default points at the vendor-shipped copy, not models/arch/: issue #6 (which commits arch
    # files under models/arch/) has not landed as of this issue (#7) — see docs/board_bringup.md.
    # Same arch as the stock build used in Step 1 (AGX3_Small_NoSoftmax.arch); pass --arch-file
    # explicitly to point at #6's committed copy once it exists, per this issue's "Do not"
    # clause (never silently swap architecture files).
    ap.add_argument("--arch-file",
                     default="$COREDLA_ROOT/example_architectures/AGX3_Small_NoSoftmax.arch")
    ap.add_argument("--expected-argmax", type=int, default=None,
                     help="argmax from the OpenVINO CPU-INT8 reference on the same record (issue #3)")
    ap.add_argument("--fclk-mhz", type=float, default=None)
    ap.add_argument("--out", default=None, help="write a results/ JSON here if given")
    ap.add_argument("--subject", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--jtag-path", default="*jtag*master*")
    args = ap.parse_args(argv)

    try:
        transport = SystemConsoleTransport(args.sof, jtag_path=args.jtag_path)
    except NotImplementedError as exc:
        print(f"smoke_infer needs a board: {exc}", file=sys.stderr)
        return 3

    input_bytes = Path(args.input).read_bytes()
    run = smoke_infer(transport, input_bytes=input_bytes, input_addr=args.input_addr,
                      output_addr=args.output_addr, output_bytes=args.output_bytes)

    ok = args.expected_argmax is None or run["argmax"] == args.expected_argmax
    print(f"argmax={run['argmax']}" + (f" expected={args.expected_argmax} "
          f"{'PASS' if ok else 'FAIL'}" if args.expected_argmax is not None else ""))

    if args.out:
        result = build_result(run, model=args.model, arch_file=args.arch_file,
                              date=args.date or _today(),
                              subject=args.subject or f"{args.model}-hostless-jtag-smoke",
                              fclk_mhz=args.fclk_mhz, expected_argmax=args.expected_argmax)
        Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
        print(f"wrote {args.out}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
