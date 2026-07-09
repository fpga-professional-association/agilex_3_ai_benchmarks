# Timing constraints for the toolchain smoke compile (issue #1). Single free-running clock —
# there is nothing else to constrain in a one-counter design.

create_clock -name clk -period 10.000 [get_ports clk]
set_false_path -from [get_ports rst_n]
