# rtl/microbench/l1_pe_array/ — L1 fmax vs PE-array size (issue #11)

PLAN §7 L1 + §3 LV1/LV4. Measures how the achievable fmax of a classic-mode INT8 PE array falls as
it grows from a couple of DSPs toward filling the device, and how much of that fall is recovered by
(a) retiming-clean RTL (LV1) and (b) isolating the hot array clock from the CSR/control logic (LV4).
Everything here comes from Quartus compile reports — no hardware (fmax is `kind: estimate`).

## Bottom line up front

**This is the _classic-mode_ PE-array fmax curve, not tensor-mode.** Issue #11's premise ("reuse #9's
known-good tensor-mode PE cell; keep the tensor-mode audit green at every grid point") is undercut by
#9's own finding: Agilex 3 tensor mode is unreachable from hand-written RTL on Quartus Prime Pro 26.1
(msg 24863; DSP Prime restricted to Stratix 10 NX + Agilex 5 — see the L0 README and the PLAN §3 LV2
/ §7 L0 caveats). There is no tensor-mode cell to instantiate. So L1 reuses #9's `l0_mac_block`
**verbatim as the classic-mode MAC core** and characterises the retiming/fmax behaviour of the
classic-mode datapath — the honest "what can custom RTL do on this silicon" number. `audit_tensor_mode.py`'s
role inverts accordingly: instead of asserting tensor-count == DSP-count, the sweep confirms the DSP
mode is *consistently classic* (`[C] DSP_PRIME = 0`) at **every** grid point — a point that silently
changed mode would still invalidate the curve.

## What the array is

A `NUM_ROWS × NUM_COLS` **weight-stationary systolic tile** (`l1_pe_array.sv`):

- **PE** = `l1_pe_cell.sv`, which wraps #9's verbatim `l0_mac_block` (a 10-lane INT8 dot product +
  DSP-cascade accumulate, ~5 classic DSPs) and adds a stationary weight register and the systolic
  data-forward register.
- **Activations** enter each row's left edge and shift right one column/cycle (systolic pipeline in
  `l1_pe_cell.data_q`); **partial sums** accumulate down each column through the DSP cascade (never
  ALM adder trees — PLAN §3 LV2); column bottoms are summed and registered into the checksum sink.
- **M20K-fed edges:** the activation stream is a real inferred, initialised, registered-read
  `ramstyle="M20K"` buffer (confirmed in the fitter RAM report: `act_mem` → Simple-Dual-Port M20K,
  1024×80, 4 blocks), read by a free-running address counter. One shared buffer feeds all rows
  (decorrelated per row by a static byte rotate) so M20K usage stays flat instead of scaling with the
  array and swamping the DSP-vs-fmax signal.
- **Non-constant operands:** weights are latched once at `load` from live per-cell LFSRs, so
  synthesis cannot fold the multiplies to LUTs (the 0-DSP trap #9 documents) yet they are held
  stationary for the run. Determinism lets the four variants produce a bit-identical checksum.

## The two variables (and nothing else)

| Axis | Values | Where it lives |
|---|---|---|
| LV1 reset style | `RETIME_CLEAN` (reset-less datapath pipeline regs, retimeable) vs `RESET_HEAVY` (sync reset on every datapath reg, shared high-fanout net) | `l1_pe_cell` / `l1_pe_array` datapath registers; `l0_mac_block`'s cascade accumulator keeps its `clear` semantics unchanged in both (it is not a retiming target) |
| LV4 clock domain | `MERGED` (CSR + array on one clock) vs `ISOLATED` (array on `clk_hot`, CSR on cool `clk`, seam via `pulse_sync`/`async_fifo`/`cdc_bit_sync`) | `l1_pe_top` generate |

`l1_pe_core` is single-clock and identical in every build; only the seam (`l1_pe_top`) and the reset
style move. `sim/l1_pe_array/run.sh` builds all four combinations and asserts they print the **same**
checksum — that equivalence is what makes any fmax delta attributable to the discipline alone (issue
"do not": only the RTL/domain variable moves). It also checks the checksum is non-trivial (real work)
and retires exactly `N_VECTORS`.

## Methodology

- Grid: `NUM_ROWS×NUM_COLS ∈ {1×2, 2×2, 2×3, 3×3, 4×4, 5×5}` (2→25 PEs, ~10→~125 nominal DSP) ×
  {clean, heavy} × {merged, isolated} = **24 compiles**. Array shape co-varies with size (a
  consequence of scaling a 2D tile whose PE is a 5-DSP block; seed and shape sweeps are future
  refinement, as the issue permits for seeds). Actual DSP/ALM/M20K are recorded per point.
- Constraints: `quartus/constraints/l1_sweep_merged.sdc` (one clock @ 300 MHz aggressive) and
  `l1_sweep_isolated.sdc` (hot `clk_hot` @ 300 MHz, cool `clk` @ 150 MHz, declared asynchronous).
- Driver: `scripts/sweep_l1.py` generates the project (`.qpf` + per-revision `.qsf`, both derived /
  gitignored), compiles each revision (`quartus_sh --flow compile`, in the Quartus container), and
  parses the Timing-Analyzer (`.sta.rpt`), Fitter (`.fit.rpt`) and **Hyper-Retiming / Fast Forward**
  (`.fit.retime.rpt`) reports into `level: "L1"` result JSONs + `results/reports/l1_curve.md`.

## Results

Full 24-point grid compiled (all `rc=0`, all classic-mode `[C] DSP_PRIME = 0`). Numbers below are
Quartus Timing-Analyzer restricted-fmax estimates (worst corner); table + JSONs in
`results/reports/l1_curve.md` and `results/l1_pe-array-fmax-*.json`.

**fmax vs size (clean/merged):**

| grid | DSP | ALM | M20K | fmax (MHz) |
|---|---|---|---|---|
| 1x2 | 10 | 259 | 4 | **61.83** |
| 2x2 | 20 | 347 | 4 | 51.01 |
| 2x3 | 30 | 449 | 4 | 51.22 |
| 3x3 | 45 | 578 | 4 | 50.48 |
| 4x4 | 80 | 906 | 4 | 50.82 |
| 5x5 | 125 | 1364 | 4 | 49.42 |

The curve is essentially **flat at ~50 MHz** across a 12× DSP range. The one outlier is the smallest
1×2 (61.8 MHz): with a single row there is *no* vertical cascade, so its critical path is just the
10-lane dot product; every larger grid adds the cascade-accumulate add into the path and settles onto
the ~50 MHz plateau. M20K stays at 4 (the shared activation buffer, by design), so it isn't a
scaling factor; ALM grows linearly with the array as expected.

- **Cliff vs the 300 MHz target: none — 0 of 6 sizes reach 300 MHz; peak is 61.85 MHz.** There is no
  fmax-vs-size cliff in this range: the per-PE combinational depth (10-lane dot product + cascade add)
  dominates over array-scale routing/fanout all the way to 125 DSP, so growing the array barely moves
  fmax. The classic-mode custom-RTL PE is ~5–6× short of PLAN §2's aggressive 300 MHz (and ~5× short
  of the 250 MHz conservative end). **This is the measured correction to the "fabric-clock assumption"
  L1 was meant to replace: for a naïvely-tiled classic-mode MAC, plan ~50 MHz, not 250–300.**

- **LV1 delta (retime_clean − reset_heavy, merged): +9.0 MHz (+17%) at 1×2, then ≈0 (±0.55 MHz,
  within fitter noise) at every size ≥ 2×2.** Mechanism, straight from the Fast Forward report:
  **"Insufficient Registers" at every point.** The reused L0 MAC cell is single-cycle combinational,
  so the Hyper-Retimer has nothing to move and the reset style of the (few) pipeline registers stops
  mattering once the combinational MAC+cascade path fills the period. The measured cash value of LV1
  on *this* datapath is therefore ~0 beyond the smallest config — LV1 only pays once the MAC itself is
  internally pipelined (deliberately not done here, to isolate the reused cell's own ceiling). That
  "you must add pipeline depth before retiming-clean RTL buys anything" is the real LV1 takeaway.

- **LV4 delta (isolated − merged, clean): ±0.4 MHz everywhere — noise.** Putting the CSR on a separate
  150 MHz cool clock buys nothing because the limiting path is entirely inside the array; the CSR
  logic never pulled on the array clock in the merged build. In the isolated builds the cool `clk`
  trivially *meets* timing (tiny CSR at 150 MHz) while the hot `clk_hot` stays the register-starved
  limiter — the retime report shows exactly this split. LV4 pays off only when control logic actually
  shares and loads the hot clock, which a lone array datapath does not.

- **Fast Forward limiting reason:** `Insufficient Registers` on the array clock at **all** points
  (merged: `clk`; isolated: `clk_hot`); the isolated cool `clk` reports `Meets timing requirements`.
  See per-point `fast_forward_limit` in each JSON and the summary in `l1_curve.md`.

**Net:** neither LV1 nor LV4 rescues a register-starved classic-mode PE array; both levers presuppose
a datapath with pipeline depth (LV1) or shared hot-clock control (LV4). The honest L1 number feeding
PLAN §2 is a ~50 MHz classic-mode custom-RTL ceiling, ~5–6× below the planning point — reaching
250–300 MHz needs either the tensor-mode DSP (toolchain-locked off Agilex 3 per #9) or a
deeply-pipelined MAC, which is the natural follow-on microbench.

## Files

| File | Role |
|---|---|
| `l1_pe_cell.sv` | One PE: stationary weight reg + systolic data reg + verbatim `l0_mac_block` MAC; `RESET_HEAVY` knob |
| `l1_pe_array.sv` | `NUM_ROWS×NUM_COLS` weight-stationary systolic tile + M20K activation edge + column combine |
| `l1_pe_core.sv` | Single-clock hot core: array + run FSM + cycle counter + checksum |
| `l1_pe_top.sv` | CSR slave + LV4 seam (MERGED direct wires vs ISOLATED CDC wrappers) |
| `../../../quartus/l1_sweep/l1_sweep_top.sv` | Compile-only harness (power-on CSR sequencer + observable pin) — not a board bring-up top |
| `../../../sim/l1_pe_array/` | Verilator equivalence + non-triviality testbench |
| `../../../scripts/sweep_l1.py` | Grid compile driver + report parser + JSON/curve emitter |

## Hardware handoff

Every fmax here is a Quartus Timing-Analyzer static-timing **estimate** (`kind: estimate`), exactly as
the issue specifies ("All from compile reports — no hardware"). A silicon fmax confirmation would need
#7's JTAG/System Console flow and an AXC3000 board; the CSR run/readback logic is written and
equivalence-checked in simulation but has never talked to real silicon.
