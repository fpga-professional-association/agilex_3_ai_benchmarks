# Phase 10 probe: measure the CoreDLA-internal Fmax from the existing Phase 9
# snapshot, WITHOUT recompiling.  The Phase 9 combined-domain Fmax (123 MHz) is
# set by a dla_par0 M20K -> niosv_m LSU path, which is a Nios-domain path.  This
# script isolates paths whose launch AND latch registers are inside the fpga_ai
# instance, so we learn what dla_clk alone could run at.

project_open axc3000_top
create_timing_netlist -snapshot final
read_sdc
update_timing_netlist

set out [open "output_files/phase10_dla_fmax_probe.rpt" w]

proc emit {fh msg} {
    puts $fh $msg
    post_message -type info $msg
}

# --- 1. Global clock Fmax summary (for reference) -------------------------
emit $out "=== report_clock_fmax_summary ==="
foreach row [get_clock_fmax_info] {
    emit $out "  $row"
}

# --- 2. Worst setup paths ENTIRELY INSIDE fpga_ai -------------------------
set dla_regs [get_registers -nowarn {*fpga_ai*}]
emit $out ""
emit $out "=== fpga_ai register count: [get_collection_size $dla_regs] ==="

set worst_dla 99.0
foreach_in_collection p [get_timing_paths -setup -npaths 30 -detail path_only \
                            -from $dla_regs -to $dla_regs] {
    set s [get_path_info $p -slack]
    if {$s < $worst_dla} { set worst_dla $s }
}
emit $out "=== fpga_ai-internal worst setup slack (at 10.000 ns): $worst_dla ==="
emit $out "=== implied fpga_ai-internal max frequency: [expr {1000.0/(10.0 - $worst_dla)}] MHz ==="

report_timing -setup -npaths 25 -detail summary \
    -from $dla_regs -to $dla_regs \
    -file output_files/phase10_dla_internal_paths.rpt

# --- 3. Worst setup paths that CROSS the fpga_ai boundary -----------------
# These are the paths that will become genuine 100<->200 MHz crossings.
report_timing -setup -npaths 25 -detail summary \
    -from $dla_regs -to [remove_from_collection [get_registers *] $dla_regs] \
    -file output_files/phase10_dla_out_paths.rpt
report_timing -setup -npaths 25 -detail summary \
    -from [remove_from_collection [get_registers *] $dla_regs] -to $dla_regs \
    -file output_files/phase10_dla_in_paths.rpt

# --- 4. Worst setup paths with NEITHER end in fpga_ai (Nios side) ---------
report_timing -setup -npaths 15 -detail summary \
    -from [remove_from_collection [get_registers *] $dla_regs] \
    -to   [remove_from_collection [get_registers *] $dla_regs] \
    -file output_files/phase10_nios_paths.rpt

close $out
delete_timing_netlist
project_close
