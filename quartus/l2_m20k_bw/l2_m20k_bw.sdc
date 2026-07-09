# l2_m20k_bw.sdc — timing constraints for the AXC3000 L2 aggregate-M20K-bandwidth board harness
# (issue #12, PLAN §7 L2 + §3 LV3).
#
# Clock architecture: one 25 MHz board XO -> IOPLL -> clk (~300 MHz, 0deg), single domain — no
# clk2x, no I/O-periphery phase concerns (unlike the HyperBus template this harness's qsys system
# mirrors: there is no external memory interface here, just the JTAG-Avalon master + m20k_bw).
# The IOPLL-generated clock and the JTAG-to-Avalon bridge's TCK are constrained by the Qsys IP's own
# generated .sdc (pulled in via qsys/l2_sys/l2_sys.qip); here we anchor the board clock, let
# Quartus derive the PLL output, add uncertainty, and cut the async button + LEDs.

# ---- board reference clock (25 MHz on CLK_25M_C) ----
create_clock -name CLK_25M_C -period 40.000 [get_ports CLK_25M_C]

# ---- derive the IOPLL output clock (clk ~300 MHz) from the reference ----
derive_pll_clocks
derive_clock_uncertainty

# ---- asynchronous, debounced-in-firmware push button: do not time it ----
set_false_path -from [get_ports USER_BTN] -to [all_registers]

# The user LEDs are slow status indicators; cut them from timing.
set_false_path -to [get_ports {LED1 RLED GLED}]
