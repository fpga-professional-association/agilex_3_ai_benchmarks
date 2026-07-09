#!/usr/bin/env python3
"""Golden cycle-accurate model of rtl/microbench/l0_tensor_chain/l0_tensor_chain.sv (issue #9).

Mirrors the RTL's update order exactly (same Galois LFSR feedback mask, same per-role seed
function, same cascade-accumulate pipeline) so the checksum/cycle-count this model predicts can be
compared bit-for-bit against a Verilator simulation (sim/l0_tensor_chain/tb_l0_tensor_chain.sv) or a
real hardware run (sw/host/run_l0.py). Determinism vs. the RTL is the only property this model
needs — TAPS is an arbitrary fixed nonzero mask, not a claimed maximal-length polynomial (see
rtl/microbench/l0_tensor_chain/l0_lfsr.sv).

    python l0_golden.py --n-blocks 8 --n-taps 10 --n-vectors 1000
"""

from __future__ import annotations

import argparse

TAPS_UNIT = 0xB465  # must match l0_tensor_chain.sv's TAPS_UNIT localparam
FILL_MARGIN = 4      # must match l0_tensor_chain.sv's FILL_MARGIN parameter default


def taps_mask(width: int) -> int:
    """Replicate TAPS_UNIT across `width` bits (matches the SV localparam TAPS construction)."""
    unit_bits = 16
    n_units = width // unit_bits + 1
    full = 0
    for _ in range(n_units):
        full = (full << unit_bits) | TAPS_UNIT
    return full & ((1 << width) - 1)


def seed_for_role(role: int, n_taps: int) -> int:
    """Matches l0_tensor_chain.sv's seed_for_role(): one byte per tap, byte k = role*7+1+k*3 (mod 256)."""
    seed = 0
    for k in range(n_taps):
        byte = (role * 7 + 1 + k * 3) & 0xFF
        seed |= byte << (8 * k)
    return seed


def lfsr_next(state: int, width: int, taps: int) -> int:
    """One Galois-LFSR step, matching l0_lfsr.sv: state <= (state >> 1) ^ (state[0] ? TAPS : 0)."""
    feedback = state & 1
    state >>= 1
    if feedback:
        state ^= taps
    return state & ((1 << width) - 1)


def taps_bytes(state: int, n_taps: int) -> list[int]:
    return [(state >> (8 * k)) & 0xFF for k in range(n_taps)]


def to_signed8(v: int) -> int:
    return v - 256 if v & 0x80 else v


def dot_product(weight_state: int, data_state: int, n_taps: int) -> int:
    w = taps_bytes(weight_state, n_taps)
    d = taps_bytes(data_state, n_taps)
    return sum(to_signed8(w[i]) * to_signed8(d[i]) for i in range(n_taps))


def to_signed32(v: int) -> int:
    v &= 0xFFFFFFFF
    return v - (1 << 32) if v & 0x8000_0000 else v


def run(n_blocks: int, n_taps: int, n_vectors: int, fill_margin: int = FILL_MARGIN) -> dict:
    """Cycle-accurate replay of one full l0_tensor_chain run.

    Returns {"cycles": int, "checksum": int (32-bit), "done": int} — same fields the CSR snapshot
    exposes (bench_pkg::L0_ADDR_CYCLES_LO/DONE/CHECKSUM).

    `cycle` here tracks cycle_q's CURRENT (pre-increment) value at the top of each iteration, i.e.
    what the RTL's combinational `retire`/`chain_out` read that cycle — the retire decision and the
    checksum XOR use `acc[-1]` as it stood BEFORE this iteration's dot products are folded in
    (mirroring `always_ff` reading `acc_q` — last cycle's registered value — while the new
    combinational dot product is computed in parallel and only latched at the end of the cycle).
    `cycle` still increments on the last retiring iteration (cycle_q<=cycle_q+1 is unconditional
    while running_q=1 in the RTL) before the run stops, so the final cycle count is exactly
    `fill_cycles + n_vectors`.
    """
    width = 8 * n_taps
    taps = taps_mask(width)
    fill_cycles = n_blocks + fill_margin

    data_state = seed_for_role(999, n_taps)
    weight_state = [seed_for_role(b, n_taps) for b in range(n_blocks)]
    acc = [0] * n_blocks

    cycle = 0
    vector_count = 0
    checksum = 0

    while vector_count < n_vectors:
        if cycle >= fill_cycles:
            checksum ^= acc[n_blocks - 1] & 0xFFFFFFFF
            vector_count += 1
            if vector_count >= n_vectors:
                cycle += 1
                break

        # combinational this-cycle dot products, using the CURRENT (pre-advance) LFSR states —
        # matches always_comb reading state_q before the always_ff clocked update.
        dots = [dot_product(weight_state[b], data_state, n_taps) for b in range(n_blocks)]
        new_acc = [0] * n_blocks
        new_acc[0] = to_signed32(dots[0])
        for b in range(1, n_blocks):
            new_acc[b] = to_signed32(dots[b] + acc[b - 1])
        acc = new_acc

        data_state = lfsr_next(data_state, width, taps)
        weight_state = [lfsr_next(s, width, taps) for s in weight_state]
        cycle += 1

    return {"cycles": cycle, "checksum": checksum & 0xFFFFFFFF, "done": vector_count}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n-blocks", type=int, required=True)
    ap.add_argument("--n-taps", type=int, default=10)
    ap.add_argument("--n-vectors", type=int, required=True)
    ap.add_argument("--fill-margin", type=int, default=FILL_MARGIN)
    args = ap.parse_args(argv)

    result = run(args.n_blocks, args.n_taps, args.n_vectors, args.fill_margin)
    macs_per_dsp_cycle = (args.n_vectors * args.n_blocks * args.n_taps) / (result["cycles"] * args.n_blocks)
    print(f"cycles={result['cycles']} done={result['done']} checksum=0x{result['checksum']:08X} "
          f"macs_per_dsp_cycle={macs_per_dsp_cycle:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
