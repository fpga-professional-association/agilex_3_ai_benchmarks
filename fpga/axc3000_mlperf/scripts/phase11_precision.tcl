# Phase 11: full-precision slack on the DLA domain.
#
# The .sta.summary rounds to 3 decimals, which is not good enough to decide
# whether a 0.000 ns hold slack is genuinely non-negative.  get_path_info
# returns the raw float.
project_open axc3000_top
create_timing_netlist -snapshot final
read_sdc
update_timing_netlist

set ck "u0|iopll_0|niosv_lab_iopll_0_outclk1"
foreach {kind flag} {setup -setup hold -hold recovery -recovery removal -removal} {
    foreach_in_collection p [get_timing_paths $flag -npaths 1 -detail path_only -to_clock $ck] {
        puts [format "%-9s slack %.6f ns   to %s" $kind [get_path_info $p -slack] \
                  [get_node_info -name [get_path_info $p -to]]]
    }
}
puts ""
puts "--- worst 5 setup paths on dla_clk ---"
foreach_in_collection p [get_timing_paths -setup -npaths 5 -detail path_only -to_clock $ck] {
    puts [format "  %.6f  %s" [get_path_info $p -slack] \
              [get_node_info -name [get_path_info $p -to]]]
}
puts ""
puts "--- worst 5 hold paths on dla_clk ---"
foreach_in_collection p [get_timing_paths -hold -npaths 5 -detail path_only -to_clock $ck] {
    puts [format "  %.6f  %s" [get_path_info $p -slack] \
              [get_node_info -name [get_path_info $p -to]]]
}
delete_timing_netlist
project_close
