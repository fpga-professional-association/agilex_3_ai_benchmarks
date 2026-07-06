## L1 sweep — MERGED clock architecture (issue #11, PLAN §3 LV4 "merged" point).
## CSR decode and the PE array share ONE clock, constrained at PLAN §2's aggressive 300 MHz end of
## the 250-300 MHz planning window. `clk_hot` is unused in the MERGED build (l1_sweep_top ties the
## array to `clk`); it carries no clock. A negative-slack result here is expected and informative —
## it is exactly the fmax number the sweep records, not a build failure.
create_clock -name clk -period 3.333 [get_ports clk]
set_false_path -from [get_ports rst_n]
