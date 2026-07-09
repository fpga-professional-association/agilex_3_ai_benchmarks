# make_l2_sys.tcl — qsys-script that constructs + saves fpga.../quartus/l2_m20k_bw/qsys/l2_sys.qsys
#
# The on-chip control/clock backbone for the L2 aggregate-M20K-bandwidth board harness (issue #12,
# PLAN §7 L2 + §3 LV3). This is simpler than fpga/axc3000/qsys/make_bw_sys.tcl (the HyperBus
# bandwidth-test template this file mirrors the idiom of): ONE clock domain, no clk2x, no HyperBus
# device-clock plan — m20k_bw is a pure on-chip design with NO external memory / NO HyperBus pins.
#
#   * 25 MHz board clock in  (CLK_25M_C)
#   * Agilex-3 IOPLL: outclk0 = clk (~300 MHz, 0 deg) — PLAN §4's "M20K on-chip ... ~330 GB/s
#     aggregate @300 MHz" operating point, so the harness's achieved-GB/s number is directly
#     comparable to that PLAN figure without a clock-scaling fudge.
#   * reset bridge + reset controller (synchronous, active-high fabric reset, clk domain)
#   * Altera JTAG-to-Avalon-MM master bridge — its Avalon-MM master is EXPORTED to top.sv, where it
#     drives the m20k_bw CSR slave (CTRL/K/CYCLES_LO/HI/STATUS/CS_ADDR/CS_DATA/AGG_CS/DIMS).
#
# Run (headless, in the Quartus-Pro docker; cwd = quartus/l2_m20k_bw; see
# quartus/l2_m20k_bw/README.md):
#   qsys-script --script=qsys/make_l2_sys.tcl --quartus-project=l2_m20k_bw
#   qsys-generate qsys/l2_sys.qsys --synthesis=VERILOG --quartus-project=l2_m20k_bw --rev=l2_m20k_bw
#
# NOTE (verified against this exact Docker image/version, 26.1 build 110): --quartus-project=NAME
# is REQUIRED on BOTH qsys-script and qsys-generate here. Without it on qsys-script, project
# auto-creation fails outright ("Failed to create Quartus Project") because it falls back to the
# wrong default DEVICE. With --quartus-project on qsys-script, it also auto-appends the
# IP_FILE/QSYS_FILE assignments to l2_m20k_bw.qsf (see that file's comment on NOT also adding the
# generated .qip — Quartus error 19021, duplicate IP name). Without --quartus-project on
# qsys-generate, the sub-IP would be left as empty "Generic Component" black boxes and quartus_syn
# would fail with "instantiates undefined entity" (same family of issue as
# quartus/ph3_hyperram_char/qsys/make_char_clkgen.tcl documents for that build).

package require -exact qsys 26.1

# ---------------------------------------------------------------------------
create_system l2_sys
set_project_property DEVICE_FAMILY {Agilex 3}
set_project_property DEVICE       {A3CY100BM16AE7S}

# ===========================================================================
# 25 MHz board clock input
# ===========================================================================
add_instance clk_in altera_clock_bridge
set_instance_parameter_value clk_in EXPLICIT_CLOCK_RATE {25000000.0}
set_instance_parameter_value clk_in NUM_CLOCK_OUTPUTS   {1}

# ===========================================================================
# IOPLL: 25 MHz ref -> ONE output clock "clk" @ ~300 MHz, 0 deg.
# Single-clock system (no clk2x / no second phase — there is no I/O periphery to centre a phase
# against; m20k_bw's M20K banks + JTAG CSR slave all live in this one domain).
# ===========================================================================
set CLK_MHZ 300.0

add_instance iopll altera_iopll
set_instance_parameter_value iopll gui_reference_clock_frequency {25.0}
set_instance_parameter_value iopll gui_operation_mode            {direct}
set_instance_parameter_value iopll gui_use_locked                {1}
set_instance_parameter_value iopll gui_number_of_clocks          {1}
set_instance_parameter_value iopll gui_output_clock_frequency0   $CLK_MHZ
set_instance_parameter_value iopll gui_phase_shift_deg0          {0.0}

# Clock bridge: an IOPLL output clock that is BOTH exported AND fanned to internal sinks loses its
# internal connections on save (the export wins). So the internal fabric (rst_ctrl, jtag_master)
# taps iopll.outclk0 directly, while a dedicated clock bridge carries the SAME clock to the
# top-level export (mirrors make_bw_sys.tcl's clkbr0/clkbr1 idiom, here with just one bridge).
add_instance clkbr0 altera_clock_bridge
set_instance_parameter_value clkbr0 EXPLICIT_CLOCK_RATE [expr {$CLK_MHZ * 1.0e6}]
set_instance_parameter_value clkbr0 NUM_CLOCK_OUTPUTS   {1}

# ===========================================================================
# Reset in (async board button) -> synchronised, active-high
# ===========================================================================
add_instance reset_in altera_reset_bridge
set_instance_parameter_value reset_in ACTIVE_LOW_RESET  {0}
set_instance_parameter_value reset_in SYNCHRONOUS_EDGES {deassert}
set_instance_parameter_value reset_in NUM_RESET_OUTPUTS {1}
set_instance_parameter_value reset_in USE_RESET_REQUEST {0}

# Reset controller: re-synchronise the reset into the 300 MHz (outclk0) domain
add_instance rst_ctrl altera_reset_controller
set_instance_parameter_value rst_ctrl NUM_RESET_INPUTS        {1}
set_instance_parameter_value rst_ctrl SYNC_DEPTH               {3}
set_instance_parameter_value rst_ctrl OUTPUT_RESET_SYNC_EDGES  {deassert}
set_instance_parameter_value rst_ctrl RESET_REQUEST_PRESENT    {0}

# Fan the synchronised fabric reset back out to a bridge so it can be exported to top.sv
add_instance rst_out altera_reset_bridge
set_instance_parameter_value rst_out ACTIVE_LOW_RESET  {0}
set_instance_parameter_value rst_out SYNCHRONOUS_EDGES {none}
set_instance_parameter_value rst_out NUM_RESET_OUTPUTS {1}
set_instance_parameter_value rst_out USE_RESET_REQUEST {0}

# ===========================================================================
# JTAG-to-Avalon-MM master bridge — control plane only (PLAN §8 method E). Its Avalon-MM master is
# exported and drives m20k_bw's CSR slave in top.sv.
# ===========================================================================
add_instance jtag_master altera_jtag_avalon_master
set_instance_parameter_value jtag_master USE_PLI {0}

# ===========================================================================
# Connections
# ===========================================================================
# clocks
add_connection clk_in.out_clk iopll.refclk

add_connection iopll.outclk0 rst_ctrl.clk
add_connection iopll.outclk0 jtag_master.clk
add_connection iopll.outclk0 clkbr0.in_clk

# resets
add_connection reset_in.out_reset iopll.reset
add_connection reset_in.out_reset rst_ctrl.reset_in0
add_connection rst_ctrl.reset_out rst_out.in_reset
add_connection rst_ctrl.reset_out jtag_master.clk_reset

# The reset_in bridge (SYNCHRONOUS_EDGES=deassert) needs a clock domain (raw board clock).
# rst_out (SYNCHRONOUS_EDGES=none) is a pure pass-through and has no clock interface.
add_connection clk_in.out_clk reset_in.clk

# ===========================================================================
# Exports (top-level interfaces)
# ===========================================================================
set_interface_property clk_25    EXPORT_OF clk_in.in_clk
set_interface_property reset     EXPORT_OF reset_in.in_reset
set_interface_property clk       EXPORT_OF clkbr0.out_clk
set_interface_property locked    EXPORT_OF iopll.locked
set_interface_property sys_reset EXPORT_OF rst_out.out_reset
set_interface_property master    EXPORT_OF jtag_master.master

# ---------------------------------------------------------------------------
save_system qsys/l2_sys.qsys
