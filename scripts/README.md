# scripts/

Build & report automation. Everything a human or CI runs lives here as a documented CLI:

- `build.sh <quartus-project> [revision]` — headless compile wrapper
- `audit_tensor_mode.py` — parse fitter report, verify tensor-mode DSP count (merge gate, PLAN LV2)
- `report_fmax.py` — extract fmax/slack per clock from timing reports → results JSON
- `report_util.py` — ALM/DSP/M20K utilization → results JSON
- sweep drivers for L0b/L1 (compile N parameterizations, collect curves)
- `estimate.py` — drive the FPGA AI Suite performance estimator (`dla_compiler
  --fanalyze-performance`) for one (model IR, arch file, memory-BW) tuple → `results/ph0_*.json`
  (issue #6, PLAN §9 PH0)
- `sweep_estimates.sh` — full PH0 sweep: 7 models x committed `models/arch/*.arch` files x memory
  BW in {200, 250, 333, 400} MB/s, via `estimate.py`; logs every attempt (pass or fail) to
  `results/reports/ph0_sweep_attempts.csv` (issue #6)

Scripts parse report files under `quartus/**/output_files/`; they must fail loudly (nonzero exit,
clear message) when a report is missing rather than emitting partial JSON.
