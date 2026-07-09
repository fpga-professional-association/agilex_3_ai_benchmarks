#!/usr/bin/env python3
"""Golden cycle-accurate model of rtl/microbench/l2_m20k_bw/m20k_bw.sv (issue #12, PLAN §7 L2).

Mirrors the RTL bit-for-bit: same 32-bit xorshift bank-content generator
(m20k_bw_pkg::xorshift32_next / bank_seed), same address-wraparound read sequence, same
BANKED-vs-SHARED pulse ordering, and the same DRAIN_CYCLES = 1 + OUTPUT_REG latency accounting the
top-level FSM uses before it may safely sample the per-bank checksum registers (see m20k_bw.sv's
DRAIN_CYCLES header comment — this model exists specifically so that latency accounting can be
cross-checked against an INDEPENDENT implementation, not just re-derived from the same RTL package
this model deliberately does not import). Used by:

  - sim/microbench/l2_m20k_bw/tb_m20k_bw.sv's expected-value constants (generated offline, same
    convention as sw/host/l0_golden.py / tb_l0_tensor_chain.sv)
  - scripts/run_l2.py's --verify-golden hardware cross-check (issue step 3: "hardware checksum
    mismatch = the measurement is invalid, not 'close enough'")

    python l2_golden.py --num-banks 4 --addr-width 4 --k 20 --geometry banked --output-reg 1
"""

from __future__ import annotations

import argparse

MASK32 = 0xFFFFFFFF


def xorshift32_next(s: int) -> int:
    """Matches m20k_bw_pkg::xorshift32_next exactly, including the 0-is-fixed-point guard."""
    x = s & MASK32
    if x == 0:
        x = 0xACE1_2024
    x ^= (x << 13) & MASK32
    x ^= (x & MASK32) >> 17
    x ^= (x << 5) & MASK32
    return x & MASK32


def bank_seed(bank_id: int) -> int:
    """Matches m20k_bw_pkg::bank_seed exactly."""
    return xorshift32_next((0xB16B_00B5 ^ ((bank_id * 0x9E37_79B9) & MASK32)) & MASK32)


def bank_memory(bank_id: int, depth: int, data_width: int) -> list[int]:
    """Replicates m20k_bw_bank's `initial` block: full 32-bit running state, truncated to
    data_width only at the point of storage (see m20k_bw_bank.sv's header comment)."""
    word_mask = (1 << data_width) - 1
    s = bank_seed(bank_id)
    mem = []
    for _ in range(depth):
        s = xorshift32_next(s)
        mem.append(s & word_mask)
    return mem


def run(*, num_banks: int, addr_width: int, k: int, geometry: str, output_reg: int,
        data_width: int = 32) -> dict:
    """Cycle-accurate replay of one full m20k_bw run.

    `geometry`: "banked" (one port per reader, all banks fire every RUN cycle) or "shared" (single
    round-robin port, one bank fires per cycle). Returns the same fields the CSR snapshot exposes:
    cycles, per-bank checksums, and the aggregate (XOR of all banks) checksum.
    """
    if geometry not in ("banked", "shared"):
        raise ValueError(f"geometry must be 'banked' or 'shared', got {geometry!r}")
    depth = 1 << addr_width
    mems = [bank_memory(b, depth, data_width) for b in range(num_banks)]

    if k == 0:
        return {"cycles": 0, "checksums": [0] * num_banks, "agg_checksum": 0,
                "total_pulses": 0, "drain_cycles": 1 + output_reg}

    total_pulses = k * num_banks if geometry == "shared" else k
    drain_cycles = 1 + output_reg
    cycles = total_pulses + drain_cycles

    # replay the exact read order each bank sees: `addr` wraps mod depth, incrementing once per
    # rd_en pulse to that bank (matches m20k_bw_bank's `addr <= addr + 1` on rd_en).
    checksums = [0] * num_banks
    addrs = [0] * num_banks
    if geometry == "banked":
        for _ in range(k):
            for b in range(num_banks):
                checksums[b] ^= mems[b][addrs[b]]
                addrs[b] = (addrs[b] + 1) % depth
    else:  # shared round-robin: one bank advances per pulse, cycling 0..num_banks-1
        for p in range(total_pulses):
            b = p % num_banks
            checksums[b] ^= mems[b][addrs[b]]
            addrs[b] = (addrs[b] + 1) % depth

    agg = 0
    for c in checksums:
        agg ^= c

    return {"cycles": cycles, "checksums": checksums, "agg_checksum": agg,
            "total_pulses": total_pulses, "drain_cycles": drain_cycles}


def theoretical_gbps(num_banks: int, data_width: int, fclk_mhz: float) -> float:
    """PLAN §3 LV3 / §7 L2: banks * bytes/port/cycle * fclk, the "good geometry" ceiling every
    achieved GB/s number is compared against."""
    return num_banks * (data_width / 8) * (fclk_mhz * 1e6) / 1e9


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--num-banks", type=int, required=True)
    ap.add_argument("--addr-width", type=int, required=True)
    ap.add_argument("--k", type=int, required=True)
    ap.add_argument("--geometry", choices=["banked", "shared"], required=True)
    ap.add_argument("--output-reg", type=int, choices=[0, 1], required=True)
    ap.add_argument("--data-width", type=int, default=32)
    args = ap.parse_args(argv)

    result = run(num_banks=args.num_banks, addr_width=args.addr_width, k=args.k,
                 geometry=args.geometry, output_reg=args.output_reg, data_width=args.data_width)
    checksums_hex = " ".join(f"0x{c:08X}" for c in result["checksums"])
    print(f"cycles={result['cycles']} total_pulses={result['total_pulses']} "
          f"drain_cycles={result['drain_cycles']} agg_checksum=0x{result['agg_checksum']:08X}")
    print(f"per-bank checksums: {checksums_hex}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
