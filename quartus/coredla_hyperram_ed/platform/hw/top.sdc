# top.sdc -- timing constraints for the CoreDLA + AXC3000 HyperRAM platform (Track P).
#
# Clock architecture: one 25 MHz board XO (CLK_25M_C) feeds TWO IOPLLs (mirrors the vendor agx3c_jtag
# example's dla_pll/jtag_pll split, docs/ph3_integration.md):
#   * jtag_pll: outclk0 = 175 MHz (0 deg) = the whole global-memory/CSR/JTAG-master domain AND
#     CoreDLA's clk_ddr (single clock, no CDC on the memory path, docs/ph3_bridge_design.md v1);
#     outclk1 = 350 MHz (0 deg, CORE-ONLY -- feeds no I/O cell) = hyperram_0.clk2x, the DDIO_GPIO
#     IO_VARIANT's FABRIC2X CK-generator/RX-oversampling clock (third_party/hyperram/fpga/axc3000/
#     qsys/make_bw_sys.tcl is the board-proven origin of this exact clock pair).
#   * dla_pll: outclk0 = clk_dla, whatever dla_adjust_pll.tcl retunes it to for the achievable E7S
#     Fmax (unchanged from the vendor flow; this session did not re-run that retune -- synth-only
#     milestone, see docs/ph3_integration.md "25 MHz IOPLL reparam note").
#
# This file is NEW this session (top.out.sdc, the vendor's LPDDR4-EMIF-derived SDC, no longer
# describes any clock in this design). It has NOT been run through quartus_sta -- the milestone
# here is qsys-generate + quartus_syn (0 errors), not timing closure. Treat this as a structurally
# reasonable starting point for the next session's quartus_fit/quartus_sta pass, not a verified
# result (AGENTS.md: never claim a number that was not measured).

# ---- board reference clock (25 MHz on CLK_25M_C) ----
create_clock -name CLK_25M_C -period 40.000 [get_ports CLK_25M_C]

# ---- derive both IOPLLs' output clocks from the reference ----
derive_pll_clocks
derive_clock_uncertainty

# ---- HyperBus device clock hb_ck ----
# hb_ck is generated inside hyperbus_gpio_io's vendor CK cell (IO_VARIANT="DDIO_GPIO" ->
# hbgpio_ck_cell, CK_GEN="FABRIC2X" fallback) and driven out a normal output pin -- it is NOT a
# clock INTO the FPGA. Its off-chip (CK-to-DQ/RWDS) timing is a board-level, on-hardware task
# (third_party/hyperram/fpga/axc3000/bw.sdc took the same approach for its own bring-up fit); the
# HyperBus interface is false-pathed below pending that hardware bring-up pass.
set_false_path -to   [get_ports {hb_dq[*] hb_rwds hb_cs_n hb_ck hb_rst_n}]
set_false_path -from [get_ports {hb_dq[*] hb_rwds}]

# ---- asynchronous, debounced-in-firmware push button: do not time it ----
set_false_path -from [get_ports USER_BTN] -to [all_registers]
