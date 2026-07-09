# make_char_clkgen.tcl — qsys-script that builds qsys/char_clkgen.qsys for the PH3 HyperRAM
# standalone char/bring-up build (quartus/ph3_hyperram_char/, DELIVERABLE 5 of
# PH3_SUBMODULE_SPEC.md). Trimmed down from third_party/hyperram/fpga/axc3000/qsys/make_bw_sys.tcl
# (read-only reference, NOT modified — that file also builds the JTAG-to-Avalon master needed to
# drive a bandwidth test; this char build has no CSR traffic generator, so the JTAG master and its
# Avalon plumbing are dropped). What's kept, verbatim in spirit: 25 MHz board XO in -> Agilex-3
# IOPLL -> outclk0 = clk (CK word clock) + outclk1 = clk2x (SDR byte clock, exported on the IP's
# "clk90" interface name for historical parity with the submodule) -> synchronised, active-high
# fabric reset.
#
# Run (headless, in the Quartus-Pro 26.1 docker; see scripts/env.sh):
#   qsys-script --script=qsys/make_char_clkgen.tcl
#   qsys-generate qsys/char_clkgen.qsys --synthesis=VERILOG --quartus-project=ph3_hyperram_char \
#       --rev=ph3_hyperram_char
# (--quartus-project is REQUIRED — without it the sub-IP, e.g. the IOPLL, is emitted as an empty
#  black box and synthesis fails; same gotcha documented in the submodule's fpga/axc3000/README.md.)
#
# CLOCK PLAN: same speed point as the submodule's own AXC3000 bring-up (fpga/axc3000/qsys/
# make_bw_sys.tcl, CK_MHZ=175 max there) but started low for this FIRST char attempt of the PH3
# wrapper: CK_MHZ=50 -> clk=50 MHz, clk2x=100 MHz. hb_ck = clk2x/2 = 50 MHz => ~100 MB/s/direction
# ceiling on the x8 bus (submodule README table; NOT re-measured by this build). Bump CK_MHZ once
# this first attempt closes timing.

package require -exact qsys 26.1

# ---------------------------------------------------------------------------
create_system char_clkgen
set_project_property DEVICE_FAMILY {Agilex 3}
set_project_property DEVICE       {A3CY100BM16AE7S}

# ===========================================================================
# 25 MHz board clock input (CLK_25M_C, PIN_A7 -- quartus/constraints/axc3000_board.tcl)
# ===========================================================================
add_instance clk_in altera_clock_bridge
set_instance_parameter_value clk_in EXPLICIT_CLOCK_RATE {25000000.0}
set_instance_parameter_value clk_in NUM_CLOCK_OUTPUTS   {1}

# ===========================================================================
# IOPLL: 25 MHz ref -> outclk0 = clk (CK word clock), outclk1 = clk2x (SDR byte clock).
# Both outputs 0 deg -- no second PLL phase into the I/O periphery (the SDR PHY derives its
# CK-centring shift from clk2x's own negedge inside hyperbus_phy_sdr.sv; see
# third_party/hyperram/fpga/axc3000/qsys/make_bw_sys.tcl for the identical rationale, including the
# Fitter err 24403/24404 history this avoids).
# ===========================================================================
set CK_MHZ   50.0
set BYTE_MHZ [expr {2.0 * $CK_MHZ}]

add_instance iopll altera_iopll
set_instance_parameter_value iopll gui_reference_clock_frequency {25.0}
set_instance_parameter_value iopll gui_operation_mode            {direct}
set_instance_parameter_value iopll gui_use_locked                {1}
set_instance_parameter_value iopll gui_number_of_clocks          {2}
set_instance_parameter_value iopll gui_output_clock_frequency0   $CK_MHZ
set_instance_parameter_value iopll gui_phase_shift_deg0          {0.0}
set_instance_parameter_value iopll gui_output_clock_frequency1   $BYTE_MHZ
set_instance_parameter_value iopll gui_phase_shift_deg1          {0.0}

# Clock bridges: an IOPLL output that is both exported and fanned internally loses its internal
# fanout on save (the export wins), so tap iopll.outclkN directly for internal sinks (rst_ctrl) and
# carry the same clock to the export through a dedicated bridge. Same idiom as make_bw_sys.tcl.
add_instance clkbr0 altera_clock_bridge
set_instance_parameter_value clkbr0 EXPLICIT_CLOCK_RATE [expr {$CK_MHZ   * 1.0e6}]
set_instance_parameter_value clkbr0 NUM_CLOCK_OUTPUTS   {1}
add_instance clkbr1 altera_clock_bridge
set_instance_parameter_value clkbr1 EXPLICIT_CLOCK_RATE [expr {$BYTE_MHZ * 1.0e6}]
set_instance_parameter_value clkbr1 NUM_CLOCK_OUTPUTS   {1}

# ===========================================================================
# Reset in (async board button, active-low) -> synchronised, active-high, clk domain
# ===========================================================================
add_instance reset_in altera_reset_bridge
set_instance_parameter_value reset_in ACTIVE_LOW_RESET  {0}
set_instance_parameter_value reset_in SYNCHRONOUS_EDGES {deassert}
set_instance_parameter_value reset_in NUM_RESET_OUTPUTS {1}
set_instance_parameter_value reset_in USE_RESET_REQUEST {0}

add_instance rst_ctrl altera_reset_controller
set_instance_parameter_value rst_ctrl NUM_RESET_INPUTS       {1}
set_instance_parameter_value rst_ctrl SYNC_DEPTH             {3}
set_instance_parameter_value rst_ctrl OUTPUT_RESET_SYNC_EDGES {deassert}
set_instance_parameter_value rst_ctrl RESET_REQUEST_PRESENT   {0}

add_instance rst_out altera_reset_bridge
set_instance_parameter_value rst_out ACTIVE_LOW_RESET  {0}
set_instance_parameter_value rst_out SYNCHRONOUS_EDGES {none}
set_instance_parameter_value rst_out NUM_RESET_OUTPUTS {1}
set_instance_parameter_value rst_out USE_RESET_REQUEST {0}

# ===========================================================================
# Connections
# ===========================================================================
add_connection clk_in.out_clk iopll.refclk

add_connection iopll.outclk0 rst_ctrl.clk
add_connection iopll.outclk0 clkbr0.in_clk
add_connection iopll.outclk1 clkbr1.in_clk

add_connection reset_in.out_reset iopll.reset
add_connection reset_in.out_reset rst_ctrl.reset_in0
add_connection rst_ctrl.reset_out rst_out.in_reset
add_connection clk_in.out_clk reset_in.clk

# ===========================================================================
# Exports (top-level conduit names char_top.sv instantiates against)
# ===========================================================================
add_interface clk_ref clock sink
set_interface_property clk_ref EXPORT_OF clk_in.in_clk

add_interface clk clock source
set_interface_property clk EXPORT_OF clkbr0.out_clk

add_interface clk2x clock source
set_interface_property clk2x EXPORT_OF clkbr1.out_clk

add_interface locked conduit end
set_interface_property locked EXPORT_OF iopll.locked

add_interface reset_in reset sink
set_interface_property reset_in EXPORT_OF reset_in.in_reset

add_interface fabric_reset reset source
set_interface_property fabric_reset EXPORT_OF rst_out.out_reset

save_system char_clkgen.qsys
