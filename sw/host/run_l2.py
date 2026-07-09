#!/usr/bin/env python3
"""L2 aggregate-M20K-bandwidth microbench runner (issue #12, PLAN §7 L2 + §3 LV3).

Sequence: read back DIMS (NUM_BANKS/WORD_BYTES/GEOMETRY/OUTPUT_REG/ADDR_WIDTH, compile-time
constants baked into the loaded .sof), configure K (reads per reader), pulse CTRL.START, poll
STATUS.DONE, read the atomic (CYCLES_LO/HI, per-bank CS_ADDR/CS_DATA, AGG_CS) snapshot, and compute
achieved GB/s = NUM_BANKS * K * WORD_BYTES / (cycles / fclk_hz) (issue step 4/5). Cross-checks the
returned checksums against scripts/l2_golden.py's INDEPENDENT cycle-accurate model when
--verify-golden is given -- issue #12 do-not: "do not report bandwidth from a run whose checksum
failed" is enforced by raising, not warning. JTAG/System Console is control-only (PLAN §8 method
E) -- nothing timed happens on the host; all hardware I/O is behind the `Transport` abstraction
(sw/host/transport.py) so `run_microbench`'s GB/s math + checksum-compare is fully unit-testable
without a board (see sw/host/tests/test_run_l2.py's MockL2Transport).

    python run_l2.py --k 100000 --fclk-mhz 300.0 --out results/l2_m20k_bw_banked_outreg.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# scripts/ holds l2_golden.py (the independent cycle-accurate model); make it importable whether this
# CLI is run from the repo root, sw/host/, or a board Docker mount.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import l2_golden

# ---- m20k_bw CSR map (m20k_bw_pkg::L2_ADDR_*, byte offsets -- the CSR slave is ALREADY
# byte-addressed, see quartus/l2_m20k_bw/top.sv's header comment) ----
REG = {
    "CTRL":       0x00,  # W: bit0 START (self-clearing)
    "K":          0x04,  # RW (idle/done only): reads per reader for this run
    "CYCLES_LO":  0x08,  # RO: elapsed cycles, low 32 (frozen once DONE)
    "CYCLES_HI":  0x0C,  # RO: elapsed cycles, high 32
    "STATUS":     0x10,  # RO: bit0 RUNNING, bit1 DONE
    "CS_ADDR":    0x14,  # W: select bank/reader index for checksum readback
    "CS_DATA":    0x18,  # R: checksum of the bank selected by CS_ADDR
    "AGG_CS":     0x1C,  # R: XOR of every bank's checksum (frozen once DONE)
    "DIMS":       0x20,  # R: compile-time geometry (see m20k_bw_pkg.sv's field layout)
}

CTRL_START = 0
ST_RUNNING = 0
ST_DONE = 1

DEVICE = "A3CY100BM16AE7S"


def decode_dims(dims: int) -> dict:
    """Matches m20k_bw_pkg.sv's DIMS field layout exactly (see m20k_bw_pkg.sv header comment)."""
    return {
        "num_banks": dims & 0xFFFF,
        "word_bytes": (dims >> 16) & 0xFF,
        "geometry": "shared" if (dims >> 24) & 0x1 else "banked",
        "output_reg": (dims >> 25) & 0x1,
        "addr_width": (dims >> 26) & 0x3F,
    }


def run_microbench(transport, *, k: int, fclk_mhz: float, poll_interval: float = 0.01,
                   poll_timeout_s: float = 120.0, verify_golden: bool = False) -> dict:
    """Drive one run to completion and return the measured metrics + counter snapshot.

    Raises RuntimeError on a zero cycle count or (if verify_golden) a checksum mismatch against
    scripts/l2_golden.py -- issue #12 do-not: never report bandwidth from a run whose checksum
    failed.
    """
    import time

    if k <= 0:
        raise RuntimeError("k must be > 0")

    dims_raw = transport.read32(REG["DIMS"])
    dims = decode_dims(dims_raw)
    num_banks = dims["num_banks"]
    if num_banks <= 0:
        raise RuntimeError(f"DIMS readback NUM_BANKS={num_banks} is not sane -- wrong .sof loaded?")

    transport.write32(REG["K"], k)
    transport.write32(REG["CTRL"], 1 << CTRL_START)

    deadline = time.monotonic() + poll_timeout_s
    while True:
        status = transport.read32(REG["STATUS"])
        if status & (1 << ST_DONE):
            break
        if time.monotonic() > deadline:
            raise TimeoutError(f"run stalled: STATUS.DONE never set (k={k})")
        if poll_interval:
            time.sleep(poll_interval)

    cyc_lo = transport.read32(REG["CYCLES_LO"])
    cyc_hi = transport.read32(REG["CYCLES_HI"])
    cycles = (cyc_hi << 32) | cyc_lo
    if cycles == 0:
        raise RuntimeError("CYCLES is zero; cannot compute GB/s")

    bank_checksums = []
    for b in range(num_banks):
        transport.write32(REG["CS_ADDR"], b)
        bank_checksums.append(transport.read32(REG["CS_DATA"]))
    agg_checksum = transport.read32(REG["AGG_CS"])

    achieved_gbps = compute_gbps(num_banks=num_banks, k=k, word_bytes=dims["word_bytes"],
                                 fclk_mhz=fclk_mhz, cycles=cycles)
    theoretical = l2_golden.theoretical_gbps(num_banks=num_banks, data_width=dims["word_bytes"] * 8,
                                             fclk_mhz=fclk_mhz)

    golden = None
    if verify_golden:
        golden = l2_golden.run(num_banks=num_banks, addr_width=dims["addr_width"], k=k,
                               geometry=dims["geometry"], output_reg=dims["output_reg"],
                               data_width=dims["word_bytes"] * 8)
        if golden["agg_checksum"] != agg_checksum or golden["checksums"] != bank_checksums:
            raise RuntimeError(
                f"hardware result does not match scripts/l2_golden.py: "
                f"hw agg=0x{agg_checksum:08X} banks={['0x%08X' % c for c in bank_checksums]} vs. "
                f"golden agg=0x{golden['agg_checksum']:08X} banks="
                f"{['0x%08X' % c for c in golden['checksums']]} -- refusing to report GB/s "
                "(issue #12: never report bandwidth from a run whose checksum failed)")
        if golden["cycles"] != cycles:
            raise RuntimeError(
                f"hardware cycles {cycles} != golden model cycles {golden['cycles']}")

    return {
        "dims": dims,
        "k": k,
        "cycles": cycles,
        "bank_checksums": bank_checksums,
        "agg_checksum": agg_checksum,
        "golden_verified": golden is not None,
        "metrics": {
            "gbps_aggregate": achieved_gbps,
            "theoretical_gbps": theoretical,
            "efficiency_pct": (100.0 * achieved_gbps / theoretical) if theoretical > 0 else 0.0,
        },
    }


def compute_gbps(*, num_banks: int, k: int, word_bytes: int, fclk_mhz: float, cycles: int) -> float:
    """Achieved GB/s = NUM_BANKS * K * WORD_BYTES / (cycles / fclk_hz) (issue #12 step 4/5).

    Same total bytes move in every GEOMETRY config (only elapsed cycles differ), so this number is
    directly comparable across configs and against PLAN §3 LV3's banks*bytes/port/cycle*fclk bound.
    """
    if cycles <= 0:
        return 0.0
    fclk_hz = fclk_mhz * 1.0e6
    total_bytes = num_banks * k * word_bytes
    seconds = cycles / fclk_hz
    return (total_bytes / seconds) / 1.0e9


def build_result(run: dict, *, fclk_mhz: float, date: str, subject: str,
                 tool_versions: dict | None = None) -> dict:
    """Assemble a result JSON conforming to results/schema/result.schema.json."""
    d = run["dims"]
    m = dict(run["metrics"])
    m["gbps_aggregate"] = round(m["gbps_aggregate"], 4)
    m["theoretical_gbps"] = round(m["theoretical_gbps"], 4)
    m["efficiency_pct"] = round(m["efficiency_pct"], 2)
    return {
        "kind": "measured",
        "level": "L2",
        "subject": subject,
        "date": date,
        "plan_ref": "§7 L2 / §3 LV3",
        "config": {
            "device": DEVICE,
            "board": "Arrow AXC3000",
            "fclk_mhz": fclk_mhz,
            "tool_versions": tool_versions or {},
            "utilization": {"m20k": d["num_banks"]},
        },
        "metrics": m,
        "notes": (f"NUM_BANKS={d['num_banks']} WORD_BYTES={d['word_bytes']} "
                  f"GEOMETRY={d['geometry']} OUTPUT_REG={d['output_reg']} K={run['k']} "
                  f"CYCLES={run['cycles']} AGG_CHECKSUM=0x{run['agg_checksum']:08X} "
                  f"golden_verified={run['golden_verified']}; emitted by sw/host/run_l2.py."),
    }


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--k", type=int, required=True, help="reads per reader for this run")
    ap.add_argument("--fclk-mhz", type=float, required=True,
                    help="IOPLL clk frequency actually built into the loaded .sof")
    ap.add_argument("--out", required=True)
    ap.add_argument("--subject", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--verify-golden", action="store_true",
                    help="cross-check the hardware checksums against scripts/l2_golden.py")
    ap.add_argument("--poll-interval", type=float, default=0.01)
    args = ap.parse_args(argv)

    # SystemConsoleTransport is board-only; the CLI is exercised on hardware during board bring-up.
    from transport import SystemConsoleTransport
    try:
        transport = SystemConsoleTransport(csr_master="l2_m20k_bw", mem_master="")
    except NotImplementedError as exc:
        print(f"run_l2 CLI needs a board: {exc}", file=sys.stderr)
        return 3

    try:
        run = run_microbench(transport, k=args.k, fclk_mhz=args.fclk_mhz,
                             poll_interval=args.poll_interval, verify_golden=args.verify_golden)
    except (RuntimeError, TimeoutError) as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 1

    result = build_result(run, fclk_mhz=args.fclk_mhz, date=args.date or _today(),
                          subject=args.subject or f"l2-m20k-bw-{run['dims']['geometry']}"
                                                  f"-outreg{run['dims']['output_reg']}")
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}: {result['metrics']['gbps_aggregate']} GB/s "
          f"({result['metrics']['efficiency_pct']}% of theoretical, cycles={run['cycles']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
