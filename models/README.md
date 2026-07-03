# models/

- `arch/` — FPGA AI Suite architecture files (`.arch`) for Agilex 3, committed; one per
  configuration (DDR-free config (a), HyperRAM-global-memory config (b)), plus the
  performance-estimator variants.
- `downloads/`, `onnx/`, `ir/`, `compiled/` — gitignored artifact staging, produced by
  `sw/model_prep/` and `dla_compiler`. Reproduce, don't commit.

Model roster and canonical sizes: PLAN §5.
