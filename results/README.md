# results/

One JSON file per result, conforming to `schema/result.schema.json`. Naming:
`<level-or-phase>_<subject>_<yyyymmdd>[_<n>].json` (e.g. `l3_hyperbus_sustained_20260815.json`).

PLAN §10 rule: **numbers without configs are noise** — the schema makes fclk, device, bitstream/
report references, and tool versions mandatory. `kind` distinguishes `estimate` (desk numbers,
PH0 estimator) from `measured` (hardware) from `reference` (software accuracy baselines); a
measurement never gets overwritten by an estimate.

Rendered tables/plots go in `results/reports/` (generated, committed).
