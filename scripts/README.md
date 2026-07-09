# scripts/

Build & report automation. Everything a human or CI runs lives here as a documented CLI:

- `build.sh <quartus-project> [revision]` — headless compile wrapper
- `audit_tensor_mode.py` — parse fitter report, verify tensor-mode DSP count (merge gate, PLAN LV2)
- `report_fmax.py` — extract fmax/slack per clock from timing reports → results JSON
- `report_util.py` — shared Quartus report parsing (ALM/DSP/M20K utilization, Fmax Summary) used by
  `report_fmax.py` and the sweep drivers
- `sweep_l0b.py` — L0b soft-MAC grid (W x M): generates `quartus/l0b_soft_mac/` revisions, compiles
  each, writes `results/*.json` + `results/reports/l0b_soft_mac_curve.md` (issue #10)
- sweep drivers for other levels (e.g. L1) follow the same pattern

Scripts parse report files; they must fail loudly (nonzero exit, clear message) when a report is
missing rather than emitting partial JSON.

**Correction (issue #9):** this used to say reports live under `quartus/**/output_files/`. Real
compiles in this Docker-backed toolchain (docs/toolchain.md, issue #1) put per-run reports/logs/
state directly in the Quartus project directory instead (e.g. `quartus/l0_tensor_chain/
l0_tensor_chain_n1.fit.rpt`), which is why `.gitignore` ignores `quartus/**/*.rpt` etc. rather than
an `output_files/` subtree. `scripts/audit_tensor_mode.py` takes explicit report paths from its
caller for exactly this reason — it does not need to guess the layout.
