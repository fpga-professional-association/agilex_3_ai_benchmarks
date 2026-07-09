#!/usr/bin/env python3
"""L3 HyperBus characterization runner: train -> memtest -> shmoo -> sustained-BW (issue #14,
PLAN §7 L3).

Orchestrates the three L3 devices (sw/host/hyperbus.py: HbTrainer, L3Memtest, L3BwEngine) over one
`Transport` (JTAG control plane only, PLAN §8 method E — never the timed data path; the devices
themselves free-run the timed portion in fabric and only the resulting counters are read back).
Two subcommands, one measurement config per invocation (same convention as run_bench.py):

    shmoo-point   train the capture window, then run a memtest at the current HyperBus clock, and
                  emit one `results/` JSON with window width + error rate for that clock (issue #14
                  step 1-3). Run once per clock step in the 100->133->166->200 MHz sweep (PLAN §7 L3
                  step 3); a clock change on this design means a full Quartus recompile with a
                  different .sdc period (see module docstring note on PLL vs recompile below), so
                  each invocation corresponds to a separate bitstream.

    sustain       at the already-chosen operating point, stream one burst-length's worth of
                  back-to-back linear bursts and emit one `results/` JSON with sustained MB/s +
                  efficiency (issue #14 step 4/5). Run once per burst length in {64B,256B,1KB,4KB}.

Clock-change mechanism (recompile vs PLL reconfig): this design has no PLL/clock-reconfiguration IP
anywhere in the repository yet (checked `platform_designer/` and `quartus/` — no `pll_*.qsys`,
no `altera_pll`/`iopll` reference, no dynamic reconfiguration controller). hbmc_core's `hb_ck` is
driven directly from its `clk` input (rtl/hyperbus/hyperbus_top.sv), i.e. the current design assumes
a fixed external/system clock; producing a different HyperBus clock therefore means a fresh Quartus
compile against a different `.sdc` clock period, one bitstream per shmoo point, NOT a runtime PLL
reconfiguration. This is a documented recommendation based on reading the current RTL/constraints,
not a verified board fact — introducing a reconfigurable PLL is out of this issue's scope (it would
be new Platform Designer / clocking RTL work) and is called out again in the PR's Hardware Handoff
section.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

from hyperbus import HbTrainer, L3BwEngine, L3Memtest

DEVICE = "A3CY100BM16AE7S"
DEFAULT_SEED = 0xACE1

# Placeholder CSR base addresses for the three L3 devices in the (not-yet-built) Platform Designer
# address map -- TBD during system integration/board bring-up (docs/hyperbus.md #14 addendum). Kept
# as named constants (not repeated literals) so the three devices never collide in the meantime.
DEFAULT_TRAINER_BASE = 0x0000
DEFAULT_MEMTEST_BASE = 0x1000
DEFAULT_BW_BASE = 0x2000


def run_shmoo_point(transport, *, span_words: int, pass_target: int, seed: int = DEFAULT_SEED,
                    base_addr: int = 0, min_window: int = 2, poll_interval: float = 0.01,
                    trainer_csr_base: int = DEFAULT_TRAINER_BASE,
                    memtest_csr_base: int = DEFAULT_MEMTEST_BASE) -> dict:
    """Train the capture window, then memtest at the current clock. Raises RuntimeError if the
    trained window doesn't meet `min_window` (issue #14 step 1: "window must be >= 2 taps wide to
    accept") -- the caller should treat that as "this clock is the first unstable point" (step 3).

    `base_addr` is the HyperRAM word address the memtest reads/writes (memtest's own config
    register); `trainer_csr_base`/`memtest_csr_base` are each device's own CSR block base address in
    the host's Avalon address map -- unrelated to `base_addr`, see the module docstring."""
    trainer = HbTrainer(transport, base=trainer_csr_base)
    window = trainer.train(poll_interval=poll_interval)
    if not window["valid"] or window["width"] < min_window:
        raise RuntimeError(
            f"capture window too narrow to accept: width={window['width']} (need >= {min_window}), "
            f"valid={window['valid']} -- this clock is unstable (PLAN §7 L3 step 3)")

    memtest = L3Memtest(transport, base=memtest_csr_base)
    mt = memtest.run(seed=seed, base_addr=base_addr, span_words=span_words,
                     pass_target=pass_target, poll_interval=poll_interval)
    return {"window": window, "memtest": mt}


def build_shmoo_result(run: dict, *, hyperbus_mhz: float, fclk_mhz: float, date: str,
                       tool_versions: dict | None = None, plan_ref: str = "§4") -> dict:
    """Assemble a result JSON conforming to results/schema/result.schema.json."""
    window, mt = run["window"], run["memtest"]
    return {
        "kind": "measured",
        "level": "L3",
        "subject": f"hyperbus-shmoo-{hyperbus_mhz:g}mhz",
        "date": date,
        "plan_ref": plan_ref,
        "config": {
            "device": DEVICE,
            "board": "Arrow AXC3000",
            "fclk_mhz": fclk_mhz,
            "hyperbus_mhz": hyperbus_mhz,
            "tool_versions": tool_versions or {},
        },
        "metrics": {
            "window_width_taps": window["width"],
            "window_center_tap": window["center"],
            "window_valid": window["valid"],
            "error_rate": round(mt["error_rate"], 9) if mt["error_rate"] == mt["error_rate"] else None,
            "err_count": mt["err_count"],
            "n_records": mt["pass_done"],
        },
        "notes": (f"emitted by sw/host/run_l3.py shmoo-point; window=[{window['lo']},{window['hi']}] "
                  f"center={window['center']}; memtest {mt['pass_done']} pass(es) over "
                  f"{mt.get('err_count', 0)} error(s)."),
    }


def run_sustain(transport, *, burst_bytes: int, burst_count: int, fclk_mhz: float,
               hyperbus_mhz: float, base_addr: int = 0, dir_read: bool = True,
               poll_interval: float = 0.01, bw_csr_base: int = DEFAULT_BW_BASE) -> dict:
    if burst_bytes % 2 != 0:
        raise ValueError("burst_bytes must be even (16-bit Avalon words)")
    bw = L3BwEngine(transport, base=bw_csr_base)
    res = bw.run(base_addr=base_addr, burst_words=burst_bytes // 2, burst_count=burst_count,
                dir_read=dir_read, fclk_mhz=fclk_mhz, poll_interval=poll_interval)
    peak_mbps = 2 * hyperbus_mhz  # HyperBus is 8-bit DDR: bytes/s = 2 x f_HB (PLAN §4)
    res["efficiency_pct"] = (100.0 * res["sustained_mbps"] / peak_mbps) if peak_mbps > 0 else float("nan")
    return res


def build_sustain_result(res: dict, *, burst_bytes: int, burst_count: int, fclk_mhz: float,
                         hyperbus_mhz: float, dir_read: bool, date: str,
                         tool_versions: dict | None = None, plan_ref: str = "§4") -> dict:
    return {
        "kind": "measured",
        "level": "L3",
        "subject": f"hyperbus-sustained-{burst_bytes}b",
        "date": date,
        "plan_ref": plan_ref,
        "config": {
            "device": DEVICE,
            "board": "Arrow AXC3000",
            "fclk_mhz": fclk_mhz,
            "hyperbus_mhz": hyperbus_mhz,
            "tool_versions": tool_versions or {},
        },
        "metrics": {
            "sustained_mbps": round(res["sustained_mbps"], 4),
            "efficiency_pct": round(res["efficiency_pct"], 3),
            "burst_bytes": burst_bytes,
            "burst_count": burst_count,
            "direction": "read" if dir_read else "write",
            "bursts_done": res["bursts_done"],
            "cycles": res["cycles"],
        },
        "notes": (f"emitted by sw/host/run_l3.py sustain; {res['bursts_done']} back-to-back "
                  f"{burst_bytes}-byte bursts, {res['cycles']} cycles @ {fclk_mhz} MHz."),
    }


def _today() -> str:
    return datetime.date.today().isoformat()


def _connect():
    """SystemConsoleTransport is board-only; both subcommands are exercised on hardware during #18."""
    from transport import SystemConsoleTransport
    return SystemConsoleTransport(csr_master="l3", mem_master="hyperram")


def _cmd_shmoo_point(args) -> int:
    try:
        transport = _connect()
    except NotImplementedError as exc:
        print(f"run_l3 shmoo-point needs a board: {exc}", file=sys.stderr)
        return 3
    try:
        run = run_shmoo_point(transport, span_words=args.span_words, pass_target=args.pass_target,
                              seed=args.seed, base_addr=args.base_addr,
                              min_window=args.min_window, poll_interval=args.poll_interval)
    except RuntimeError as exc:
        print(f"shmoo-point failed: {exc}", file=sys.stderr)
        return 1
    result = build_shmoo_result(run, hyperbus_mhz=args.hyperbus_mhz, fclk_mhz=args.fclk_mhz,
                                date=args.date or _today())
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}: window_width={result['metrics']['window_width_taps']} taps, "
          f"error_rate={result['metrics']['error_rate']}")
    return 0


def _cmd_sustain(args) -> int:
    try:
        transport = _connect()
    except NotImplementedError as exc:
        print(f"run_l3 sustain needs a board: {exc}", file=sys.stderr)
        return 3
    run = run_sustain(transport, burst_bytes=args.burst_bytes, burst_count=args.burst_count,
                      fclk_mhz=args.fclk_mhz, hyperbus_mhz=args.hyperbus_mhz,
                      base_addr=args.base_addr, dir_read=not args.write,
                      poll_interval=args.poll_interval)
    result = build_sustain_result(run, burst_bytes=args.burst_bytes, burst_count=args.burst_count,
                                  fclk_mhz=args.fclk_mhz, hyperbus_mhz=args.hyperbus_mhz,
                                  dir_read=not args.write, date=args.date or _today())
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}: {result['metrics']['sustained_mbps']} MB/s "
          f"({result['metrics']['efficiency_pct']}% of 2xf_HB peak)")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p1 = sub.add_parser("shmoo-point", help="train + memtest at the current HyperBus clock")
    p1.add_argument("--hyperbus-mhz", type=float, required=True)
    p1.add_argument("--fclk-mhz", type=float, required=True)
    p1.add_argument("--span-words", type=int, default=4096)
    p1.add_argument("--pass-target", type=int, default=100,
                    help="issue #14 acceptance: >= 100 full-device passes at the operating point")
    p1.add_argument("--seed", type=lambda s: int(s, 0), default=DEFAULT_SEED)
    p1.add_argument("--base-addr", type=lambda s: int(s, 0), default=0)
    p1.add_argument("--min-window", type=int, default=2)
    p1.add_argument("--poll-interval", type=float, default=0.01)
    p1.add_argument("--out", required=True)
    p1.add_argument("--date", default=None)
    p1.set_defaults(func=_cmd_shmoo_point)

    p2 = sub.add_parser("sustain", help="sustained-BW sweep point at the operating clock")
    p2.add_argument("--burst-bytes", type=int, required=True, choices=[64, 256, 1024, 4096])
    p2.add_argument("--burst-count", type=int, default=1000)
    p2.add_argument("--hyperbus-mhz", type=float, required=True)
    p2.add_argument("--fclk-mhz", type=float, required=True)
    p2.add_argument("--base-addr", type=lambda s: int(s, 0), default=0)
    p2.add_argument("--write", action="store_true", help="write direction (default: read)")
    p2.add_argument("--poll-interval", type=float, default=0.01)
    p2.add_argument("--out", required=True)
    p2.add_argument("--date", default=None)
    p2.set_defaults(func=_cmd_sustain)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
