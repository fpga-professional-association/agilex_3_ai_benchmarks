#!/usr/bin/env python3
"""Record the measured on-silicon HyperBus bandwidth sweep as schema-valid result JSONs.

Implements the results-capture half of PH3 / PLAN §4-§5: the HyperRAM (W957D8NB, x8 HyperBus) is the
AXC3000's only external RAM, and CoreDLA is memory-bound on it (see docs/ai-model blocker), so its
sustained bandwidth is a headline board number. The bandwidth engine + PHY live in the
``third_party/hyperram`` submodule's ``fpga/axc3000`` example (``bw.sof``); a host reads the on-chip
WR/RD cycle counters back over JTAG (control-plane only — the counters cover the on-chip Avalon
datapath, never JTAG access time, so the derived MB/s is the true HyperBus throughput; PLAN §8
method E is respected because JTAG never carries the benchmarked data stream).

This script does NOT talk to the board. It transcribes the raw, integrity-verified cycle counts that
``sysconsole/bw_read.tcl`` printed on real silicon (each point had ERR_COUNT=0 / RESULT=PASS) and
derives MB/s deterministically, emitting one JSON per burst-length point under ``results/``. Re-run
the board sweep to refresh MEASURED; keep the raw cycle counts here as the source of truth so the
MB/s math is reproducible and reviewable.

Measured 2026-07-09 on the physical Arrow AXC3000 (this repo's box, USB-Blaster III over usbipd),
Quartus Pro 26.1 Build 110, bitstream ``bw.sof`` sha256 8328e85b…, hyperram repo commit c6f5d2b,
CK (word clock) = 175 MHz, byte clock = 350 MHz (SDR PHY ceiling operating point).

Usage:
    python sw/host/record_hyperbus_bw.py [--out-dir results] [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import reslib  # noqa: E402

# --- measurement provenance (all points share this) ------------------------------------------------
DATE = "2026-07-09"
CK_MHZ = 175.0            # IOPLL outclk0 = HyperBus CK word clock = on-chip fabric clk
BYTE_MHZ = 350.0          # IOPLL outclk1 = SDR byte clock (2 x CK)
BYTES_PER_WORD = 2        # x8 HyperBus, DDR-on-the-wire but SDR PHY -> 2 bytes/word/CK here
BITSTREAM_SHA = "8328e85b4e4e88121bab3fd3a91c7f550029a3f31ddc31fe436e3e452a99aa7d"
HYPERRAM_COMMIT = "c6f5d2b"
QUARTUS = "26.1.0 Build 110"

# --- raw measured primitive: (LEN_words, wr_cycles, rd_cycles) at CK=175 MHz, single burst ---------
# Straight from bw_read.tcl on hardware; every point returned ERR_COUNT=0 / RESULT=PASS.
SWEEP = [
    # LEN,  wr_cyc, rd_cyc
    (16,    33,     45),
    (64,    81,     93),
    (128,   145,    157),
    (256,   273,    285),
    (512,   529,    541),
    (768,   785,    797),   # SDR ceiling burst (>=1024 words hits the tCSM ~15 us refresh window)
]


def mbps(len_words: int, cycles: int) -> float:
    """MB/s = LEN * bytes/word * f_clk / (cycles * 1e6). Mirrors bw_read.tcl's `mbps`."""
    return round(len_words * BYTES_PER_WORD * (CK_MHZ * 1e6) / (cycles * 1e6), 2)


def build_result(len_words: int, wr_cyc: int, rd_cyc: int) -> dict:
    wr = mbps(len_words, wr_cyc)
    rd = mbps(len_words, rd_cyc)
    return {
        "kind": "measured",
        "level": "PH3",
        "subject": f"hyperbus-sustained-bw-len{len_words}",
        "date": DATE,
        "plan_ref": "§5 table",
        "config": {
            "device": "A3CY100BM16AE7S",
            "board": "Arrow AXC3000",
            "fclk_mhz": CK_MHZ,
            "hyperbus_mhz": CK_MHZ,
            "tool_versions": {"quartus": QUARTUS},
            "utilization": {"alm": 1423, "dsp": 0, "m20k": 2},
            "bitstream_sha256": BITSTREAM_SHA,
            "report_paths": [
                "third_party/hyperram/fpga/axc3000/output_files/bw.fit.summary",
                "third_party/hyperram/fpga/axc3000/output_files/bw.sta.summary",
            ],
        },
        "metrics": {
            "sustained_mbps": wr,          # headline = WRITE MB/s
            "write_mbps": wr,
            "read_mbps": rd,
            "wr_cycles": wr_cyc,
            "rd_cycles": rd_cyc,
            "burst_words": len_words,
            "byte_clock_mhz": BYTE_MHZ,
            "cold_pass": False,            # resident single-burst, not a first-touch-from-cold pass
        },
        "notes": (
            f"MEASURED on the physical Arrow AXC3000 (A3CY100BM16AE7S + Winbond W957D8NB HyperRAM) "
            f"2026-07-09 over USB-Blaster III. Single HyperBus burst of {len_words} words at "
            f"CK={CK_MHZ:.0f} MHz (SDR PHY, byte clock {BYTE_MHZ:.0f} MHz), integrity ERR_COUNT=0 / "
            f"RESULT=PASS. WRITE {wr} MB/s ({wr_cyc} clk cycles), READ {rd} MB/s ({rd_cyc} cycles). "
            f"JTAG is control-plane only: the counters time the on-chip Avalon datapath, not JTAG "
            f"access, so MB/s is the true HyperBus throughput (PLAN §8 method E respected). "
            f"Bandwidth scales with burst length as the fixed CA+latency overhead "
            f"(~{wr_cyc - len_words} clk WR / ~{rd_cyc - len_words} clk RD) amortizes. Bandwidth "
            f"engine + SDR PHY from third_party/hyperram @ {HYPERRAM_COMMIT}; bitstream bw.sof "
            f"sha256 {BITSTREAM_SHA[:12]}…; Quartus {QUARTUS}. Peak of this sweep "
            f"(len=768): 342.42 W / 337.26 R MB/s = the SDR PHY ceiling on this board; the "
            f"200 MHz/400 MB/s device max needs the DDIO PHY (blocked, see submodule README)."
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out-dir", default=str(REPO_ROOT / "results"), help="where to write result JSONs")
    ap.add_argument("--check", action="store_true", help="validate only; do not write files")
    args = ap.parse_args()

    validator = reslib.make_validator()
    out_dir = Path(args.out_dir)
    written = []
    for len_words, wr_cyc, rd_cyc in SWEEP:
        result = build_result(len_words, wr_cyc, rd_cyc)
        errors = reslib.validate_result(result, validator)
        if errors:
            print(f"INVALID result for len={len_words}:", *errors, sep="\n  ", file=sys.stderr)
            return 1
        if not args.check:
            path = out_dir / f"ph3_hyperbus_bw_len{len_words:04d}_{DATE.replace('-', '')}.json"
            path.write_text(json.dumps(result, indent=2) + "\n")
            written.append(path)
    if args.check:
        print(f"OK: {len(SWEEP)} HyperBus bandwidth results schema-valid (not written).")
    else:
        for p in written:
            print(f"wrote {p.relative_to(REPO_ROOT)}")
        print(f"OK: {len(written)} schema-valid measured results written.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
