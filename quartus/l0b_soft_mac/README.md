# quartus/l0b_soft_mac/

PLAN §7 L0b: soft-logic MAC density (MACs/kALM) + fmax at INT4/INT2/INT1, one Quartus project
revision per (W, M) grid point, driven entirely by `scripts/sweep_l0b.py` (issue #10).

- `l0b_soft_mac.qpf` and every `w<W>_m<M>.qsf` here are **generated** by `scripts/sweep_l0b.py`
  (`write_project_files`) — don't hand-edit, rerun the script instead. Top-level entity:
  `soft_mac_array` (`rtl/microbench/l0b_soft_mac/`). Constraints: `quartus/constraints/l0b_soft_mac.sdc`.
- Reports land in `output_files/` (gitignored, per `.gitignore`'s `quartus/**/output_files/`).

## Deliberate deviation from the `quartus/README.md` convention

Every other project in this repo is meant to be reproducible with a single
`scripts/build.sh <project>` call, i.e. `quartus_sh --flow compile`, which includes the Assembler
and produces a `.sof`. **This project's sweep does not do that.** `scripts/sweep_l0b.py` runs only
`quartus_syn` → `quartus_fit` → `quartus_sta` per grid point, and never `quartus_asm`.

Why: PLAN §7 L0b's deliverable is "entirely from compile reports, no hardware needed" — ALM
utilization (Fitter) and fmax (Timing Analyzer) are all this level measures, and this design is
never programmed onto the board. Skipping the Assembler also sidesteps a real mechanical problem:
this microbench's top-level ports (`clk`, `rst_n`, `checksum_q`) have no real board pinout (there's
nothing to pin them to — this isn't a board bring-up), so the Assembler would refuse to emit a
`.sof` anyway (Critical Warning 25196/25207: unassigned pin locations / I/O standards), the same
gate `quartus/smoke/smoke.qsf`'s comments describe working around for a real board-facing smoke
test. Rather than invent placeholder pin/IO-standard assignments for ~9+ grid points purely to
satisfy a bitstream nobody needs, this project stops one stage earlier. `quartus_fit` alone already
fully places and routes the design (needed for a real ALM count) and `quartus_sta` runs a full
timing analysis against `quartus/constraints/l0b_soft_mac.sdc` — both stages that matter for this
issue's numbers run to completion normally.

If a future issue needs an actual `.sof` for one of these grid points (e.g. to hand-verify fractal
synthesis on real silicon), add pin/IO-standard assignments to that revision's QSF (same technique
as `quartus/smoke/smoke.qsf`) and run `quartus_asm` manually — `scripts/sweep_l0b.py` itself
intentionally stays report-only.
