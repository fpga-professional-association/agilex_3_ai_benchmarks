# Base SDC for the AXC3000 board clock architecture (issue #7). Constrains only the raw
# board-level ports from axc3000_board.tcl (25 MHz input oscillator, active-low reset button).
# A project with its own PLL/IOPLL downstream of CLK_25M_C must add `derive_pll_clocks` +
# `derive_clock_uncertainty` on top of this file, same pattern as the vendor's own
# first_agilex3_refdes/sources/axc3000_top.sdc.

set_time_format -unit ns -decimal_places 3

# 25 MHz fixed input oscillator (User Guide SS3.1.1 -- see axc3000_board.tcl).
create_clock -name CLK_25M_C -period 40.000 [get_ports {CLK_25M_C}]

# USER_BTN is a mechanical, debounced-in-fabric input -- not a clock-relative timing path.
set_false_path -from [get_ports {USER_BTN}]
