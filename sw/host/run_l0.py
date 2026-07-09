#!/usr/bin/env python3
"""L0 tensor-chain microbench runner — the host control plane for one run (issue #9, PLAN §7 L0).

Sequence: read back N_BLOCKS (cross-check against what the caller expects the loaded .sof to be),
configure N_VECTORS, START, poll STATUS.DONE, read the atomic (CYCLES/DONE/CHECKSUM) snapshot, and
compute achieved MACs/DSP/cycle = known_MAC_count / (cycles * N_BLOCKS) (issue step 6). Cross-checks
the returned checksum against sw/host/l0_golden.py's cycle-accurate model when --verify-golden is
given. Emits one schema-valid results/ JSON. JTAG/System Console is control-only (PLAN §8 method E)
— nothing timed happens on the host.

    python run_l0.py --n-blocks 8 --n-vectors 1000000 --fclk-mhz 59.63 \\
        --out results/l0_tensor_chain_n8.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import l0_golden
import l0_regs
from l0_regs import L0TensorChain

DEVICE = "A3CY100BM16AE7S"
N_TAPS = 10   # must match rtl/microbench/l0_tensor_chain/l0_tensor_chain.sv's N_TAPS default


def run_microbench(transport, *, n_blocks: int, n_vectors: int, fclk_mhz: float,
                   poll_interval: float = 0.1, poll_timeout_s: float = 600.0,
                   verify_golden: bool = False) -> dict:
    """Drive one run to completion and return the measured metrics + counter snapshot.

    Raises RuntimeError on an N_BLOCKS mismatch (wrong .sof loaded), a DONE_COUNT mismatch, a zero
    cycle span, or (if verify_golden) a checksum mismatch against the golden model.
    """
    l0 = L0TensorChain(transport)
    hw_n_blocks = l0.n_blocks()
    if hw_n_blocks != n_blocks:
        raise RuntimeError(
            f"N_BLOCKS CSR readback {hw_n_blocks} != expected {n_blocks} — wrong .sof loaded?")
    if n_vectors <= 0:
        raise RuntimeError("n_vectors must be > 0")

    l0.configure(n_vectors)
    l0.start()

    deadline = time.monotonic() + poll_timeout_s
    while not l0.is_done():
        if time.monotonic() > deadline:
            raise TimeoutError(f"run stalled: STATUS.DONE never set (n_vectors={n_vectors})")
        time.sleep(poll_interval)

    counters = l0.snapshot()
    if counters.done != n_vectors:
        raise RuntimeError(f"DONE_COUNT {counters.done} != expected {n_vectors}")
    if counters.cycles == 0:
        raise RuntimeError("CYCLES_64 is zero; cannot compute MACs/DSP/cycle")

    known_macs = n_vectors * n_blocks * N_TAPS
    macs_per_dsp_cycle = known_macs / (counters.cycles * n_blocks)

    golden = None
    if verify_golden:
        golden = l0_golden.run(n_blocks, N_TAPS, n_vectors)
        if golden["checksum"] != counters.checksum or golden["cycles"] != counters.cycles:
            raise RuntimeError(
                f"hardware result does not match sw/host/l0_golden.py: "
                f"hw checksum=0x{counters.checksum:08X} cycles={counters.cycles} vs. "
                f"golden checksum=0x{golden['checksum']:08X} cycles={golden['cycles']}")

    return {
        "counters": counters,
        "n_blocks": n_blocks,
        "n_vectors": n_vectors,
        "known_macs": known_macs,
        "golden_verified": golden is not None,
        "metrics": {
            "macs_per_dsp_cycle": macs_per_dsp_cycle,
        },
    }


def build_result(run: dict, *, fclk_mhz: float, date: str, subject: str,
                 tool_versions: dict | None = None) -> dict:
    """Assemble a result JSON conforming to results/schema/result.schema.json."""
    m = dict(run["metrics"])
    m["macs_per_dsp_cycle"] = round(m["macs_per_dsp_cycle"], 4)
    return {
        "kind": "measured",
        "level": "L0",
        "subject": subject,
        "date": date,
        "plan_ref": "§7 L0",
        "config": {
            "device": DEVICE,
            "board": "Arrow AXC3000",
            "fclk_mhz": fclk_mhz,
            "tool_versions": tool_versions or {},
            "utilization": {"dsp": run["n_blocks"], "dsp_tensor_mode": 0},
        },
        "metrics": m,
        "notes": (f"N_BLOCKS={run['n_blocks']} N_VECTORS={run['n_vectors']} "
                  f"CYCLES_64={run['counters'].cycles} CHECKSUM=0x{run['counters'].checksum:08X} "
                  f"golden_verified={run['golden_verified']}; emitted by sw/host/run_l0.py. "
                  "dsp_tensor_mode=0: see rtl/microbench/l0_tensor_chain/README.md — tensor-mode "
                  "WYSIWYG instantiation is rejected by this Quartus version for FAMILY \"Agilex 3\" "
                  "(this is the classic-mode fallback, not the tensor-mode target)."),
    }


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n-blocks", type=int, required=True)
    ap.add_argument("--n-vectors", type=int, required=True)
    ap.add_argument("--fclk-mhz", type=float, required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--subject", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--verify-golden", action="store_true",
                    help="cross-check the hardware checksum/cycles against sw/host/l0_golden.py")
    ap.add_argument("--poll-interval", type=float, default=0.1)
    args = ap.parse_args(argv)

    # SystemConsoleTransport is board-only; the CLI is exercised on hardware during board bring-up
    # (#7/#8, not yet closed at the time of writing).
    from transport import SystemConsoleTransport
    try:
        transport = SystemConsoleTransport(csr_master="l0_tensor_chain", mem_master="")
    except NotImplementedError as exc:
        print(f"run_l0 CLI needs a board: {exc}", file=sys.stderr)
        return 3

    try:
        run = run_microbench(transport, n_blocks=args.n_blocks, n_vectors=args.n_vectors,
                             fclk_mhz=args.fclk_mhz, poll_interval=args.poll_interval,
                             verify_golden=args.verify_golden)
    except (RuntimeError, TimeoutError) as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 1

    result = build_result(run, fclk_mhz=args.fclk_mhz, date=args.date or _today(),
                          subject=args.subject or f"l0-tensor-chain-n{args.n_blocks}")
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}: {result['metrics']['macs_per_dsp_cycle']} MACs/DSP/cycle "
          f"(cycles={run['counters'].cycles})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
