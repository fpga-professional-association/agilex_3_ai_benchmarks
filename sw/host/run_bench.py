#!/usr/bin/env python3
"""Benchmark runner — the host control plane for one model run (issue #17, PLAN §6/§9 PH4).

Sequence: load the record image into HyperRAM, configure the scoreboard, START, poll off the timed
path until DONE_COUNT reaches the target, read the atomic counter snapshot + histogram, and compute
FPS / accuracy / p50 / p99. Emits one schema-valid `results/` JSON. The host divides; the hardware
counts — nothing timed happens here (JTAG is control-only, PLAN §8 E).

    python run_bench.py --recimg kws.recimg --model ds-cnn-kws --arch-file models/arch/ddrfree.arch \
        --fclk-mhz 300 --hist-shift 4 --out results/l5_dscnn_methodB.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import latency
import load_recimg
import scoreboard as sb
from scoreboard import Scoreboard

UNLICENSED_CAP = 10_000   # PLAN §9 PH1: unlicensed IP generation caps inference requests
DEVICE = "A3CY100BM16AE7S"


def run_benchmark(transport, *, n_records: int, rec_stride: int, rec_base: int, hist_shift: int,
                  fclk_mhz: float, loops: int = 1, licensed_ip: bool = False,
                  poll_interval: float = 0.1, poll_timeout_s: float = 600.0) -> dict:
    """Drive one run to completion and return the measured metrics + counter snapshot.

    Raises RuntimeError on the unlicensed cap, a DONE_COUNT mismatch, or a zero cycle span; raises
    TimeoutError if the run never reaches the target.
    """
    target = n_records * loops
    if not licensed_ip and target > UNLICENSED_CAP:
        raise RuntimeError(
            f"unlicensed IP caps inference at {UNLICENSED_CAP:,}; requested {target:,} "
            f"({n_records:,} records x {loops} loop(s)). Generate licensed IP or lower --loops.")
    if target <= 0:
        raise RuntimeError("nothing to run (n_records * loops <= 0)")

    scb = Scoreboard(transport)
    scb.configure(n_records, rec_stride, rec_base, hist_shift)
    scb.start(loop=(loops > 1))

    deadline = time.monotonic() + poll_timeout_s
    counters = scb.snapshot()
    while counters.done < target:
        if time.monotonic() > deadline:
            raise TimeoutError(f"run stalled at DONE={counters.done} of {target}")
        time.sleep(poll_interval)
        counters = scb.snapshot()

    if counters.done != target:
        raise RuntimeError(
            f"DONE_COUNT {counters.done} != expected {target} — inferences dropped or overran "
            "(check the 10k cap and the engine handshake, PLAN §10)")
    if counters.cycles == 0:
        raise RuntimeError("CYCLES_64 is zero; cannot compute FPS")

    hist = scb.read_histogram()
    fps = counters.done * (fclk_mhz * 1e6) / counters.cycles
    return {
        "counters": counters,
        "histogram": hist,
        "hist_shift": hist_shift,
        "metrics": {
            "fps": fps,
            "accuracy_top1": counters.passes / counters.done,
            "latency_us_p50": latency.percentile_us(hist, counters.done, 50, hist_shift, fclk_mhz),
            "latency_us_p99": latency.percentile_us(hist, counters.done, 99, hist_shift, fclk_mhz),
            "latency_us_min": latency.cycles_to_us(counters.lat_min, fclk_mhz),
            "latency_us_max": latency.cycles_to_us(counters.lat_max, fclk_mhz),
            "n_records": counters.done,
        },
    }


def build_result(run: dict, *, fclk_mhz: float, model: str, arch_file: str, date: str,
                 subject: str, level: str = "L5", quantization: str = "int8-nncf-ptq",
                 feed_method: str = "B", licensed_ip: bool = False, cold_pass: bool = True,
                 tool_versions: dict | None = None, plan_ref: str = "§5 table") -> dict:
    """Assemble a result JSON conforming to results/schema/result.schema.json."""
    m = dict(run["metrics"])
    m["latency_us_p50"] = round(m["latency_us_p50"], 4)
    m["latency_us_p99"] = round(m["latency_us_p99"], 4)
    m["latency_us_min"] = round(m["latency_us_min"], 4)
    m["latency_us_max"] = round(m["latency_us_max"], 4)
    m["fps"] = round(m["fps"], 3)
    m["accuracy_top1"] = round(m["accuracy_top1"], 6)
    m["cold_pass"] = cold_pass
    return {
        "kind": "measured",
        "level": level,
        "subject": subject,
        "date": date,
        "plan_ref": plan_ref,
        "config": {
            "device": DEVICE,
            "board": "Arrow AXC3000",
            "fclk_mhz": fclk_mhz,
            "model": model,
            "arch_file": arch_file,
            "quantization": quantization,
            "feed_method": feed_method,
            "licensed_ip": licensed_ip,
            "tool_versions": tool_versions or {},
        },
        "metrics": m,
        "notes": f"CYCLES_64={run['counters'].cycles}; hist_shift={run['hist_shift']}; "
                 f"emitted by sw/host/run_bench.py.",
    }


def _today() -> str:
    import datetime
    return datetime.date.today().isoformat()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--recimg", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--arch-file", required=True)
    ap.add_argument("--fclk-mhz", type=float, required=True)
    ap.add_argument("--hist-shift", type=int, default=4)
    ap.add_argument("--rec-base", type=lambda s: int(s, 0), default=0)
    ap.add_argument("--loops", type=int, default=1)
    ap.add_argument("--feed-method", default="B", choices=["A", "B", "C", "D"])
    ap.add_argument("--quantization", default="int8-nncf-ptq")
    ap.add_argument("--licensed-ip", action="store_true")
    ap.add_argument("--cold-pass", action="store_true", help="mark this a cold (non-looped) pass")
    ap.add_argument("--level", default="L5")
    ap.add_argument("--subject", default=None)
    ap.add_argument("--date", default=None)
    ap.add_argument("--no-verify", action="store_true", help="skip read-back verification of the load")
    ap.add_argument("--poll-interval", type=float, default=0.1)
    args = ap.parse_args(argv)

    # SystemConsoleTransport is board-only; the CLI is exercised on hardware during #18.
    from transport import SystemConsoleTransport
    try:
        transport = SystemConsoleTransport(csr_master="scoreboard", mem_master="hyperram")
    except NotImplementedError as exc:
        print(f"run_bench CLI needs a board: {exc}", file=sys.stderr)
        return 3

    def prog(done, total):
        print(f"\rloading {done}/{total} B", end="", file=sys.stderr)

    manifest = load_recimg.load_and_verify(transport, Path(args.recimg), args.rec_base,
                                           verify=not args.no_verify, progress=prog)
    print("", file=sys.stderr)
    try:
        run = run_benchmark(transport, n_records=manifest["record_count"],
                            rec_stride=manifest["stride"], rec_base=args.rec_base,
                            hist_shift=args.hist_shift, fclk_mhz=args.fclk_mhz,
                            loops=args.loops, licensed_ip=args.licensed_ip,
                            poll_interval=args.poll_interval)
    except (RuntimeError, TimeoutError) as exc:
        print(f"run failed: {exc}", file=sys.stderr)
        return 1

    result = build_result(run, fclk_mhz=args.fclk_mhz, model=args.model, arch_file=args.arch_file,
                          date=args.date or _today(),
                          subject=args.subject or f"{args.model}-method{args.feed_method}",
                          level=args.level, quantization=args.quantization,
                          feed_method=args.feed_method, licensed_ip=args.licensed_ip,
                          cold_pass=args.cold_pass)
    Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(f"wrote {args.out}: {result['metrics']['fps']} FPS, "
          f"acc {result['metrics']['accuracy_top1']}, p99 {result['metrics']['latency_us_p99']} us")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
