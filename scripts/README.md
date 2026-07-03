# scripts/

Build & report automation. Everything a human or CI runs lives here as a documented CLI:

- `build.sh <quartus-project> [revision]` — headless compile wrapper
- `audit_tensor_mode.py` — parse fitter report, verify tensor-mode DSP count (merge gate, PLAN LV2)
- `report_fmax.py` — extract fmax/slack per clock from timing reports → results JSON
- `report_util.py` — ALM/DSP/M20K utilization → results JSON
- sweep drivers for L0b/L1 (compile N parameterizations, collect curves)

Scripts parse report files under `quartus/**/output_files/`; they must fail loudly (nonzero exit,
clear message) when a report is missing rather than emitting partial JSON.
