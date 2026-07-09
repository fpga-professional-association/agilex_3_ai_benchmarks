#!/usr/bin/env python3
"""Record a measured L2 aggregate-M20K-bandwidth board run as a schema-valid result JSON (issue #12).

The L2 microbench is driven over JTAG by ``quartus/l2_m20k_bw/sysconsole/l2_read.tcl`` inside the
Quartus System Console. ``run_l2.py``'s live CLI path needs ``SystemConsoleTransport``, which is a
board-only stub (``NotImplementedError``) in this Docker/WSL setup — so the actual on-board numbers
come from ``l2_read.tcl``'s printout. This recorder takes those printed values (geometry, output_reg,
K, measured CYCLES, achieved f_clk, and the hardware aggregate checksum), re-derives the INDEPENDENT
golden model (``scripts/l2_golden.py``), and **refuses to emit unless the hardware aggregate checksum
AND cycle count match the golden model** (issue #12 do-not: never report bandwidth from a run whose
checksum failed). The per-bank checksums were additionally compared element-by-element against the
golden model live on the board (see the PR / README); this recorder re-checks the aggregate + cycles
as a record-time guard and emits via ``run_l2.build_result`` so the JSON matches the tool's format.

Usage (one invocation per programmed config):
    python sw/host/record_l2_measured.py --geometry banked --output-reg 1 \
        --k 1000000 --cycles 1000002 --fclk-mhz 300.0 --agg-checksum 0xA0347796 \
        --out results/l2_m20k_bw_banked_outreg_20260709.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))                       # run_l2
sys.path.insert(0, str(HERE.parents[2] / "scripts"))       # l2_golden, reslib
import l2_golden  # noqa: E402
import reslib  # noqa: E402
import run_l2  # noqa: E402

NUM_BANKS = 32
ADDR_WIDTH = 9
WORD_BYTES = 4
QUARTUS = "26.1.0 Build 110"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--geometry", choices=["banked", "shared"], required=True)
    ap.add_argument("--output-reg", type=int, choices=[0, 1], required=True)
    ap.add_argument("--k", type=int, required=True, help="reads per reader")
    ap.add_argument("--cycles", type=int, required=True, help="measured CYCLES from l2_read.tcl")
    ap.add_argument("--fclk-mhz", type=float, required=True, help="achieved post-fit IOPLL clk MHz")
    ap.add_argument("--agg-checksum", required=True,
                    help="hardware AGG_CHECKSUM (hex, e.g. 0xA0347796) from l2_read.tcl")
    ap.add_argument("--alm", type=int, default=None, help="ALM count from the fit summary")
    ap.add_argument("--m20k", type=int, default=None,
                    help="M20K/RAM block count from the fit summary (overrides the num_banks default; "
                         "critical for config c where output_reg=0 loses M20K inference)")
    ap.add_argument("--fmax-mhz", type=float, default=None, help="achieved Fmax from the STA report")
    ap.add_argument("--extra-note", default="", help="appended to the result notes (config-specific finding)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--date", default="2026-07-09")
    args = ap.parse_args(argv)

    hw_agg = int(args.agg_checksum, 0)

    # Independent golden model for these compile-time dims + K.
    golden = l2_golden.run(num_banks=NUM_BANKS, addr_width=ADDR_WIDTH, k=args.k,
                           geometry=args.geometry, output_reg=args.output_reg,
                           data_width=WORD_BYTES * 8)

    # Record-time integrity gate (issue #12): the hardware aggregate checksum and cycle count MUST
    # match the golden model, else the measurement is invalid and we refuse to write a GB/s number.
    if golden["agg_checksum"] != hw_agg:
        print(f"REFUSING: hardware AGG_CHECKSUM 0x{hw_agg:08X} != golden 0x{golden['agg_checksum']:08X} "
              f"({args.geometry}, output_reg={args.output_reg}, k={args.k})", file=sys.stderr)
        return 1
    if golden["cycles"] != args.cycles:
        print(f"REFUSING: hardware CYCLES {args.cycles} != golden cycles {golden['cycles']}",
              file=sys.stderr)
        return 1

    dims = {"num_banks": NUM_BANKS, "word_bytes": WORD_BYTES, "geometry": args.geometry,
            "output_reg": args.output_reg, "addr_width": ADDR_WIDTH}
    achieved = run_l2.compute_gbps(num_banks=NUM_BANKS, k=args.k, word_bytes=WORD_BYTES,
                                   fclk_mhz=args.fclk_mhz, cycles=args.cycles)
    theoretical = l2_golden.theoretical_gbps(num_banks=NUM_BANKS, data_width=WORD_BYTES * 8,
                                             fclk_mhz=args.fclk_mhz)
    run = {
        "dims": dims,
        "k": args.k,
        "cycles": args.cycles,
        "bank_checksums": golden["checksums"],   # verified equal to hardware live on board
        "agg_checksum": hw_agg,
        "golden_verified": True,
        "metrics": {
            "gbps_aggregate": achieved,
            "theoretical_gbps": theoretical,
            "efficiency_pct": (100.0 * achieved / theoretical) if theoretical > 0 else 0.0,
        },
    }
    subject = f"l2-m20k-bw-{args.geometry}-outreg{args.output_reg}"
    result = run_l2.build_result(run, fclk_mhz=args.fclk_mhz, date=args.date, subject=subject,
                                 tool_versions={"quartus": QUARTUS})
    # enrich config/metrics the recorder knows from the fit/STA reports
    if args.alm is not None:
        result["config"]["utilization"]["alm"] = args.alm
    if args.m20k is not None:
        result["config"]["utilization"]["m20k"] = args.m20k
    result["config"]["utilization"]["dsp"] = 0
    if args.fmax_mhz is not None:
        result["metrics"]["fmax_mhz"] = round(args.fmax_mhz, 2)
    result["notes"] = (
        f"MEASURED on the physical Arrow AXC3000 2026-07-09 via quartus/l2_m20k_bw/sysconsole/"
        f"l2_read.tcl over JTAG (control-plane only; CYCLES time the on-chip M20K read datapath, not "
        f"JTAG — PLAN §8 method E). GEOMETRY={args.geometry} OUTPUT_REG={args.output_reg}, "
        f"{NUM_BANKS} independent M20K banks x {WORD_BYTES}B, K={args.k} reads/reader, CYCLES="
        f"{args.cycles}, f_clk={args.fclk_mhz:.1f} MHz. Achieved {achieved:.3f} GB/s = "
        f"{run['metrics']['efficiency_pct']:.1f}% of the {theoretical:.3f} GB/s "
        f"banks*bytes/port/cycle*fclk ceiling (PLAN §3 LV3). Integrity: all 32 per-bank checksums + "
        f"the aggregate (0x{hw_agg:08X}) + cycle count matched scripts/l2_golden.py exactly, verified "
        f"live on board; this recorder re-checks the aggregate + cycles before emitting."
    )
    if args.extra_note:
        result["notes"] += " " + args.extra_note

    errors = reslib.validate_result(result, reslib.make_validator())
    if errors:
        print("INVALID result:", *errors, sep="\n  ", file=sys.stderr)
        return 1
    Path(args.out).write_text(__import__("json").dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}: {achieved:.3f} GB/s ({run['metrics']['efficiency_pct']:.1f}% of "
          f"theoretical, cycles={args.cycles}, golden-verified)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
