# Phase 10 per-domain timing report.
#
# The .sta.summary collapses everything into one worst number per clock, which
# is not enough here: we need the 100 MHz Nios domain and the 200 MHz CoreDLA
# domain reported separately, plus the achieved Fmax of each, so we can say
# honestly what the DLA core clock can actually close at.

project_open axc3000_top
create_timing_netlist -snapshot final
read_sdc
update_timing_netlist

puts "=================== PHASE 10 PER-DOMAIN TIMING ==================="

puts ""
puts "--- clock definitions ---"
foreach_in_collection c [all_clocks] {
    set nm [get_clock_info $c -name]
    set per [get_clock_info $c -period]
    if {$per > 0} {
        puts [format "  %-46s period %8.3f ns  (%7.2f MHz)" $nm $per [expr {1000.0/$per}]]
    } else {
        puts [format "  %-46s period %8.3f ns" $nm $per]
    }
}

puts ""
puts "--- achieved Fmax per clock (report_clock_fmax_summary) ---"
foreach row [get_clock_fmax_info] {
    puts "  $row"
}

puts ""
puts "--- worst slack per clock, per analysis type ---"
foreach ck [get_clock_fmax_info] {
    set nm [lindex $ck 0]
    foreach {kind flag} {setup -setup hold -hold recovery -recovery removal -removal} {
        set worst 1e30
        set found 0
        foreach_in_collection p [get_timing_paths $flag -npaths 1 -detail path_only \
                                     -to_clock $nm] {
            set s [get_path_info $p -slack]
            if {$s < $worst} { set worst $s ; set found 1 }
        }
        if {$found} {
            puts [format "  %-46s %-9s worst slack %8.3f ns %s" \
                      $nm $kind $worst [expr {$worst < 0 ? "  <<< VIOLATED" : ""}]]
        }
    }
}

puts ""
puts "--- worst 15 setup paths overall (where is the real limit?) ---"
report_timing -setup -npaths 15 -detail summary \
    -file output_files/phase10_worst_setup.rpt
foreach_in_collection p [get_timing_paths -setup -npaths 15 -detail path_only] {
    puts [format "  slack %8.3f  %s" [get_path_info $p -slack] \
              [get_node_info -name [get_path_info $p -to]]]
}

delete_timing_netlist
project_close
