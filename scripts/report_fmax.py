#!/usr/bin/env python3
"""Extract fmax per clock from a Quartus Timing Analyzer report (issue #10).

Thin CLI over ``report_util.parse_fmax_summary`` / ``slow_corner_fmax_mhz``. Used standalone for
spot-checking a compile's timing report, and imported by ``scripts/sweep_l0b.py`` (and future sweep
drivers, e.g. L1) to pull the slow-corner fmax number that lands in a results JSON's
``metrics.fmax_mhz``.

Usage:
    python scripts/report_fmax.py quartus/l0b_soft_mac/output_files/w4_m256.sta.rpt
    python scripts/report_fmax.py <path/to/*.sta.rpt> --clock clk --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import report_util


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("sta_rpt", type=Path, help="path to a Quartus <revision>.sta.rpt file")
    parser.add_argument("--clock", default=None, help="restrict to this clock name (default: all)")
    parser.add_argument(
        "--restricted", action="store_true",
        help="report Restricted Fmax instead of plain Fmax (see report_util.slow_corner_fmax_mhz "
             "docstring for why plain Fmax is the default: on this device, Restricted Fmax is "
             "pinned to a fixed clock-network floor, not the design's logic-limited fmax)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    args = parser.parse_args(argv)

    try:
        rows = report_util.parse_fmax_summary(args.sta_rpt)
        slow_fmax = report_util.slow_corner_fmax_mhz(rows, clock_name=args.clock, restricted=args.restricted)
    except report_util.ReportParseError as exc:
        print(f"report_fmax: {exc}", file=sys.stderr)
        return 1

    label = "restricted" if args.restricted else "plain"
    if args.json:
        print(
            json.dumps(
                {
                    "rows": [r.__dict__ for r in rows],
                    "slow_corner_fmax_mhz": slow_fmax,
                    "column": label,
                },
                indent=2,
            )
        )
    else:
        for r in rows:
            print(
                f"clock={r.clock_name!r} fmax={r.fmax_mhz} MHz restricted={r.restricted_fmax_mhz} MHz "
                f"corner={r.corner!r} note={r.note!r}"
            )
        print(f"\nslow-corner {label} fmax: {slow_fmax} MHz")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
