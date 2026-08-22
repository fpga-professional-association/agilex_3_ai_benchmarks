package require -exact qsys 26.1

# Phase 10: split the CoreDLA core clock out of the single 100 MHz domain.
#
# Before: iopll_0.outclk0 (100 MHz) -> EVERYTHING, including all three
#         fpga_ai clock inputs (dla_clk, ddr_clk, irq_clk).
# After : iopll_0.outclk1 (200 MHz) -> fpga_ai.dla_clk ONLY.
#         iopll_0.outclk0 (100 MHz) -> everything else, unchanged.
#
# Why this is safe (established from the IP's own sources, not assumed):
#   * core_hw.tcl binds csr_axi and ddr_axi to ddr_clk and irq_level to
#     irq_clk.  NO externally visible interface is associated with dla_clk --
#     it is purely the internal datapath clock.  Platform Designer therefore
#     does not need to insert any clock-crossing bridge, and the generated
#     interconnect is bit-identical to Phase 9.
#   * Every ddr_clk<->dla_clk path inside the IP already goes through vendor
#     CDC hardware: dla_dma.sv intercept_config_clock_crosser /
#     scratchpad_update_clock_crosser (ddr->dla dcfifos), dla_top.sv
#     feature_writer_clock_crosser (dla->ddr dcfifo) and
#     cc_core_start_streaming (dla_clock_cross_full_sync).
#   * dla_resetn is documented in dla_top_wrapper as "NOT synchronized to any
#     clock"; dla_platform_reset.sv re-synchronizes it per domain and
#     recombines through dla_cdc_reset_async.  So the existing single reset
#     connection stays exactly as it is -- no second reset bridge is needed.
#   * The IP ships dla_clock_cross_sync.sdc (already sourced by this project)
#     which false-paths the synchronizer first stages by wildcard, so it keeps
#     working whatever the clock relationship is.  We add NO false paths of
#     our own.
#   * The vendor's own Agilex 3 reference platform (agx3c_jtag) runs dla_clk
#     from a dedicated PLL at a different frequency from ddr_clk, and ships
#     dla_adjust_pll.tcl specifically to retune dla_clk alone.

# The .qsys caches each instance's interface footprint.  iopll_0 is a generic
# component backed by the external ip/NIOSV_lab/NIOSV_lab_iopll_0.ip, which
# ip-deploy has just re-parameterised from 1 to 2 output clocks.  Without this
# refresh the cached footprint still lists only outclk0 and add_connection
# fails -- and because qsys-script does not abort on that error, save_system
# would then persist a system with dla_clk left dangling.  That is precisely
# the trap called out in rebuild_fpga_ai_instance.tcl.
reload_component_footprint iopll_0
puts "reloaded iopll_0 footprint; interfaces now:"
foreach iface [get_instance_interfaces iopll_0] {
    puts "  $iface"
}

set changed 0

# Drop the old 100 MHz feed to the DLA core clock.
foreach c [get_connections] {
    if {[string equal $c "iopll_0.outclk0/fpga_ai.dla_clk"]} {
        remove_connection $c
        set changed 1
        puts "removed  iopll_0.outclk0/fpga_ai.dla_clk"
    }
}
if {!$changed} {
    puts "WARNING: iopll_0.outclk0/fpga_ai.dla_clk was not present"
}

# Feed it from the new 200 MHz output instead.
add_connection iopll_0.outclk1 fpga_ai.dla_clk
puts "added    iopll_0.outclk1/fpga_ai.dla_clk"

puts ""
puts "=== resulting clock connections ==="
foreach c [get_connections] {
    if {[string equal [get_connection_property $c TYPE] "clock"]} {
        puts "  $c"
    }
}

# HARD GATE.  Never save unless dla_clk is genuinely driven by outclk1 and
# nothing else still drives it from outclk0.  add_connection only warns on
# failure, so an unguarded save_system is how a broken system gets persisted.
set have_new 0
set have_old 0
foreach c [get_connections] {
    if {[string equal $c "iopll_0.outclk1/fpga_ai.dla_clk"]} { set have_new 1 }
    if {[string equal $c "iopll_0.outclk0/fpga_ai.dla_clk"]} { set have_old 1 }
}
if {!$have_new || $have_old} {
    error "REFUSING TO SAVE: dla_clk wiring is wrong (outclk1=$have_new outclk0=$have_old)"
}
puts "verified: fpga_ai.dla_clk is driven by iopll_0.outclk1 only"

sync_sysinfo_parameters
save_system
puts ""
puts "system saved"
