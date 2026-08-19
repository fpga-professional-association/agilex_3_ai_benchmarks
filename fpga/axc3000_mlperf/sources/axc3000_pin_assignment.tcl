# SPDX-FileCopyrightText: Copyright (C) 2025 Arrow Electronics, Inc.
# SPDX-License-Identifier: MIT-0
#
# AXC3000 board assignments retained from the official Arrow reference design.
# HyperRAM is represented only by safe inactive top-level ports; no controller
# or licensed SLL/xSPI/QSPI logic is used.

set_location_assignment PIN_A7  -to CLK_25M_C
set_instance_assignment -name IO_STANDARD "1.3-V LVCMOS" -to CLK_25M_C
set_location_assignment PIN_A12 -to USER_BTN
set_instance_assignment -name IO_STANDARD "1.3-V LVCMOS" -to USER_BTN
set_instance_assignment -name WEAK_PULL_UP_RESISTOR ON -to USER_BTN

# Arrow FTDI bridge (debug UART header / COM5 path).
set_location_assignment PIN_AG23 -to DBG_RX
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to DBG_RX
set_location_assignment PIN_AG24 -to DBG_TX
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to DBG_TX

set_location_assignment PIN_AJ24 -to VSEL_1V3
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to VSEL_1V3
set_location_assignment PIN_AG21 -to LED1
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to LED1
set_location_assignment PIN_AH22 -to RLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to RLED
set_location_assignment PIN_AK21 -to GLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to GLED
set_location_assignment PIN_AK20 -to BLED
set_instance_assignment -name IO_STANDARD "3.3-V LVCMOS" -to BLED

# HyperRAM pins: fixed safe inactive levels / high-Z data bus in axc3000_top.
set_location_assignment PIN_A6 -to HR_RWDS
set_instance_assignment -name IO_STANDARD "1.3-V LVCMOS" -to HR_RWDS
set_location_assignment PIN_F7 -to HR_HRESETn
set_instance_assignment -name IO_STANDARD "1.3-V LVCMOS" -to HR_HRESETn
set_location_assignment PIN_D8 -to HR_CSn
set_instance_assignment -name IO_STANDARD "1.3-V LVCMOS" -to HR_CSn
set_location_assignment PIN_D7 -to HR_CLK
set_instance_assignment -name IO_STANDARD "1.3-V LVCMOS" -to HR_CLK
set_location_assignment PIN_C3 -to HR_DQ[0]
set_location_assignment PIN_C2 -to HR_DQ[1]
set_location_assignment PIN_B4 -to HR_DQ[2]
set_location_assignment PIN_B6 -to HR_DQ[3]
set_location_assignment PIN_D3 -to HR_DQ[4]
set_location_assignment PIN_A4 -to HR_DQ[5]
set_location_assignment PIN_B3 -to HR_DQ[6]
set_location_assignment PIN_C6 -to HR_DQ[7]
foreach p {HR_DQ[0] HR_DQ[1] HR_DQ[2] HR_DQ[3] HR_DQ[4] HR_DQ[5] HR_DQ[6] HR_DQ[7]} {
    set_instance_assignment -name IO_STANDARD "1.3-V LVCMOS" -to $p
}
