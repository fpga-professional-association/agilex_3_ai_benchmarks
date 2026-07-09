# pins.tcl — AXC3000 pin + I/O-standard assignments for top.sv (issue #12 L2 board harness).
#
# Values copied verbatim from /home/tcovert/projects/hyperram/fpga/axc3000/pins.tcl (itself sourced
# from agilex_3_ai_benchmarks/quartus/constraints/axc3000_board.tcl — Arrow "AXC3000 Evaluation
# Board: User Guide" v1.2.1, cross-checked vs refdes-agilex3), for ONLY the ports top.sv actually
# uses: the 25 MHz clock, the reset button, and the 3 status LEDs. This harness is ON-CHIP ONLY —
# NO HyperBus/memory pins are assigned (no hb_* ports exist on top.sv).
#
# Sourced from l2_m20k_bw.qsf after FAMILY/DEVICE are set.

########################################################################
# 25 MHz board clock (fixed XO, single-ended)
set_location_assignment PIN_A7  -to CLK_25M_C
set_instance_assignment -name IO_STANDARD "1.2 V" -to CLK_25M_C
# PIN_A7 is a general (non-dedicated-PLL-refclk) I/O on this device, so the 25 MHz reference cannot
# reach the IOPLL on a dedicated route (Fitter error 23527). Promote it to the global clock network,
# which the Fitter accepts as an IOPLL refclk source.
set_instance_assignment -name GLOBAL_SIGNAL "GLOBAL CLOCK" -to CLK_25M_C

########################################################################
# Reset — active-low USER button (S2), needs internal weak pull-up
set_location_assignment PIN_A12 -to USER_BTN
set_instance_assignment -name IO_STANDARD "1.2 V" -to USER_BTN
set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to USER_BTN

########################################################################
# User LEDs (active-low, 3.3-V LVCMOS)
set_location_assignment PIN_AG21 -to LED1
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to LED1
set_location_assignment PIN_AH22 -to RLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to RLED
set_location_assignment PIN_AK21 -to GLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to GLED
