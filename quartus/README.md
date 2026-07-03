# quartus/

Quartus Prime Pro project revisions, one directory per experiment (e.g. `l0_tensor_chain/`,
`l1_sweep/`, `bench_config_a/`). Device: **A3CY100BM16AE7S**.

- Every project buildable headless: `quartus_sh --flow compile <project> -c <revision>` — wrapped by
  `scripts/build.sh`. No GUI-only state.
- Constraints live in `constraints/` (shared `.sdc` per clock architecture + board pinout `.tcl`).
- Build outputs are gitignored; results are extracted from reports into `results/` JSON by scripts.
- Merge gate: `scripts/audit_tensor_mode.py` must report every intended tensor-mode DSP actually in
  tensor mode (PLAN §3 LV2 — classic-mode fallback is a silent 10× loss).
