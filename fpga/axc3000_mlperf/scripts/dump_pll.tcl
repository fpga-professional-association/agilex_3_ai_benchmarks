package require -exact qsys 26.1

puts "=== ALL iopll_0 PARAMETERS ==="
if {[catch { set plist [get_instance_parameters iopll_0] } err]} {
    puts "  get_instance_parameters failed: $err"
    set plist {}
}
puts "  count = [llength $plist]"
foreach p $plist {
    set v "<unreadable>"
    catch { set v [get_instance_parameter_value iopll_0 $p] }
    puts "  $p = $v"
}
