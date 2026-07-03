# platform_designer/

Platform Designer (`.qsys`) systems. Target composition for the benchmark harness (PLAN §9 PH3):
JTAG-Avalon master (or Nios V/g) + mSGDMA + HyperBus controller + FPGA AI Suite inference IP +
scoreboard, with the clock-domain split from PLAN §3 LV4 (hot PE domain / cool control domain /
HyperBus domain, async FIFOs at seams).

Keep `.qsys` files committed; generated HDL is gitignored (regenerate with
`qsys-generate <sys>.qsys --synthesis=VERILOG`).
