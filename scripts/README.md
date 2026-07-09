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
- `estimate.py` — drive the FPGA AI Suite performance estimator (`dla_compiler
  --fanalyze-performance`) for one (model IR, arch file, memory-BW) tuple → `results/ph0_*.json`
  (issue #6, PLAN §9 PH0)
- `sweep_estimates.sh` — full PH0 sweep: 7 models x committed `models/arch/*.arch` files x memory
  BW in {200, 250, 333, 400} MB/s, via `estimate.py`; logs every attempt (pass or fail) to
  `results/reports/ph0_sweep_attempts.csv` (issue #6)

Scripts parse report files; they must fail loudly (nonzero exit, clear message) when a report is
missing rather than emitting partial JSON.

**Correction (issue #9):** this used to say reports live under `quartus/**/output_files/`. Real
compiles in this Docker-backed toolchain (docs/toolchain.md, issue #1) put per-run reports/logs/
state directly in the Quartus project directory instead (e.g. `quartus/l0_tensor_chain/
l0_tensor_chain_n1.fit.rpt`), which is why `.gitignore` ignores `quartus/**/*.rpt` etc. rather than
an `output_files/` subtree. `scripts/audit_tensor_mode.py` takes explicit report paths from its
caller for exactly this reason — it does not need to guess the layout.
