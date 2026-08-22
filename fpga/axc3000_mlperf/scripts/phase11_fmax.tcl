# Phase 11: per-domain achieved Fmax.
#
# phase10_timing.tcl's get_clock_fmax_info returns an empty list in this
# Quartus build, so read the Fmax panel the Timing Analyzer itself produces.
project_open axc3000_top
create_timing_netlist -snapshot final
read_sdc
update_timing_netlist

report_clock_fmax_summary -file output_files/phase11_fmax.rpt
report_timing -setup -npaths 15 -detail full_path \
    -file output_files/phase11_worst_setup.rpt
report_timing -setup -npaths 1 -detail full_path -panel_name "Phase11 worst"

delete_timing_netlist
project_close
