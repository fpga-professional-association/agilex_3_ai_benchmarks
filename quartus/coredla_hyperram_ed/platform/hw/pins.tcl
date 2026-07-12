# pins.tcl -- AXC3000 pin + I/O-standard assignments for the CoreDLA+HyperRAM platform top.sv.
#
# Values copied verbatim from third_party/hyperram/fpga/axc3000/pins.tcl (itself sourced from
# quartus/constraints/axc3000_board.tcl, cross-checked against two independent provenance sources --
# see that file's header), for exactly the ports THIS top.sv exposes (CLK_25M_C, USER_BTN, hb_*).
# No LEDs here: unlike the submodule's bandwidth-test top.sv, this platform's top.sv does not drive
# any board LEDs.
#
# RESOLVED on real silicon (third_party/hyperram, 2026-07-08): hb_cs_n / hb_ck use the Arrow refdes
# values D8/D7 (NOT quartus/constraints/axc3000_board.tcl's own C7/B5, which predates that
# resolution -- see this repo's docs/board_bringup.md "HyperRAM pin discrepancy" and the
# third_party pins.tcl header for the full story). AXC3000 HyperRAM is SINGLE-ENDED: there is no
# hb_ck_n board pin, so top.sv does not expose one.

########################################################################
# 25 MHz board clock (fixed XO, single-ended)
set_location_assignment PIN_A7  -to CLK_25M_C
set_instance_assignment -name IO_STANDARD "1.2 V" -to CLK_25M_C
# PIN_A7 is a general (non-dedicated-PLL-refclk) I/O on this device, so the 25 MHz reference cannot
# reach the IOPLL on a dedicated route (Fitter error 23527). Promote it to the global clock network,
# which the Fitter accepts as an IOPLL refclk source.
set_instance_assignment -name GLOBAL_SIGNAL "GLOBAL CLOCK" -to CLK_25M_C

########################################################################
# Reset -- active-low USER button (S2), needs internal weak pull-up
set_location_assignment PIN_A12 -to USER_BTN
set_instance_assignment -name IO_STANDARD "1.2 V" -to USER_BTN
set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to USER_BTN

########################################################################
# HyperRAM (Winbond W957D8NB, 1.2 V, x8 HyperBus DDR)
set_location_assignment PIN_C3 -to hb_dq[0]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[0]
set_location_assignment PIN_C2 -to hb_dq[1]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[1]
set_location_assignment PIN_B4 -to hb_dq[2]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[2]
set_location_assignment PIN_B6 -to hb_dq[3]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[3]
set_location_assignment PIN_D3 -to hb_dq[4]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[4]
set_location_assignment PIN_A4 -to hb_dq[5]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[5]
set_location_assignment PIN_B3 -to hb_dq[6]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[6]
set_location_assignment PIN_C6 -to hb_dq[7]
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_dq[7]

set_location_assignment PIN_A6 -to hb_rwds
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_rwds

set_location_assignment PIN_F7 -to hb_rst_n
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_rst_n

# CSn / CLK -- silicon-resolved values (see header note above)
set_location_assignment PIN_D8 -to hb_cs_n
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_cs_n
set_location_assignment PIN_D7 -to hb_ck
set_instance_assignment -name IO_STANDARD "1.2 V" -to hb_ck

########################################################################
# Track B fix: hb_wstrb_partial_seen / hb_hi_addr_seen (the HyperRAM subsystem's sticky debug
# trip-wires) were left with no pin assignment at all -- top.sv's header comment calls them
# "optional; e.g. drive board LEDs" but this platform never actually wired them to any LED, so
# quartus_asm refused to emit a .sof ("A programming file will not be generated because the
# assembler identified user pins missing pin location assignments", top.fit.rpt "I/O Assignment
# Warnings"). Wire them to two of the AXC3000's spare user LEDs (verified board locations, from
# quartus/constraints/axc3000_board.tcl / third_party/hyperram/fpga/axc3000/pins.tcl -- this
# platform drives no other LEDs, so RLED/GLED are free).
set_location_assignment PIN_AH22 -to hb_wstrb_partial_seen
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to hb_wstrb_partial_seen
set_location_assignment PIN_AK21 -to hb_hi_addr_seen
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to hb_hi_addr_seen
