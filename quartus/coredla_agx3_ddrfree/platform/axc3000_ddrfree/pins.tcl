# pins.tcl -- AXC3000 pin + I/O-standard assignment for the DDR-free CoreDLA example design.
#
# The DDR-free top.sv (ddrfree_common/top.sv) exposes exactly ONE board pin: clk_sys_100m_p, the
# system reference clock. On the Arrow AXC3000 the only fixed clock source is the 25 MHz XO on
# PIN_A7 at 1.2 V (there is no 100 MHz clock on this board -- the port name is the vendor's, the
# frequency is 25 MHz; board.tcl's PLLs and top.sdc are both configured for 25 MHz). Everything
# else (JTAG TCK/TMS/TDI/TDO, reset via the JTAG System Source IP) is on dedicated pins and needs
# no location assignment.
#
# Values verbatim from the proven-on-silicon AXC3000 clock assignment
# (quartus/coredla_hyperram_ed/platform/hw/pins.tcl CLK_25M_C block; itself from
# quartus/constraints/axc3000_board.tcl). PIN_A7 is a general I/O, NOT a dedicated PLL refclk route,
# so it must be promoted to the global clock network or the Fitter errors (23527).

set_location_assignment PIN_A7 -to clk_sys_100m_p
set_instance_assignment -name IO_STANDARD "1.2 V" -to clk_sys_100m_p
set_instance_assignment -name GLOBAL_SIGNAL "GLOBAL CLOCK" -to clk_sys_100m_p
