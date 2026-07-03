# sim/

Self-checking testbenches, one subdirectory per DUT (`sim/scoreboard/`, `sim/replay/`, …).

- Modules without device primitives: Verilator (`verilator --binary --timing`), exit code 0 = pass,
  nonzero + `$fatal` message = fail. Provide a `run.sh` per testbench.
- Modules with device primitives (DSP/M20K/IO): validated by Quartus compile reports instead; the
  owning issue lists which report values to check.
- Testbenches print a final `PASS` / `FAIL: <reason>` line — CI and humans grep for it.
