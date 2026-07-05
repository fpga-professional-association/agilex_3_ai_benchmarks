# models/

- `arch/` — FPGA AI Suite architecture files (`.arch`) for Agilex 3, committed, unmodified vendor
  examples (issue #6). See `arch/README.md` for which of the 3 shipped files maps to which PLAN §9
  PH3 config, and two load-bearing findings from actually running the estimator against them
  (only one is INT8-capable; none is genuinely DDR-free).
- `downloads/`, `onnx/`, `ir/`, `compiled/` — gitignored artifact staging, produced by
  `sw/model_prep/` and `dla_compiler`. Reproduce, don't commit.

Model roster and canonical sizes: PLAN §5.
