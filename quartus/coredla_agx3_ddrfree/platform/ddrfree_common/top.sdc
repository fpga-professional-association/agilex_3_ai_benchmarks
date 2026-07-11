#****************************************************************************
#
# SPDX-License-Identifier: MIT-0
# Copyright(c) 2019-2021 Altera Corporation.
#
#****************************************************************************
#
# Sample SDC for Agilex GHRD.
#
#****************************************************************************
# JTAG clock constraints
source ./jtag_example.sdc

set_time_format -unit ns -decimal_places 3

# AXC3000 board input clock. NOTE: the port is still named clk_sys_100m_p (vendor DDR-free top.sv
# port name, kept verbatim so ddrfree_common/top.sv is unmodified), but the *physical* clock on the
# Arrow AXC3000 is the 25 MHz fixed XO (CLK_25M_C, PIN_A7) -- the board has no 100 MHz source. The
# kernel_pll/sys_pll in board.tcl are configured for a 25 MHz refclk (-> 300 MHz clk_dla), and the
# IOPLL-generated output-clock SDC derives its periods as ratios of THIS create_clock, so this MUST
# be 40 ns (25 MHz), not the vendor's 10 ns -- otherwise STA would analyze clk_dla at ~1200 MHz.
create_clock -name {clk_sys_100m_p} -period 40.000 -waveform {0 20} {clk_sys_100m_p}

