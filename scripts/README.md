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

Scripts parse report files under `quartus/**/output_files/`; they must fail loudly (nonzero exit,
clear message) when a report is missing rather than emitting partial JSON.
